"""End-to-end pipeline.

  1 parse          screenplays -> beats (code)
  2 shortlist      heuristic windows per episode + stitched cold-opens (code)
  3 pick           LLM picks promos per episode by beat id (LLM, 1 call / episode)
  4 stitch         LLM builds cross-episode promos from highlight beats (LLM, 1 call)
  5 repair+check   snap cuts, fix length, validate hook / cliff / length (code)
  6 rank           composite score + LLM posting order (code + 1 LLM call)
  7 blind judge    a context-free "stranger" rates the top clips (1 LLM call, QA only)
  8 write          JSON / CSV / Markdown / HTML report + quality stats
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

from .candidates import generate, describe, heuristic_score
from .core import Show, Promo, Segment, iou
from .llm import LLM, parse_json, PendingPrompts
from .parser import load_show, sec_to_ts, long_path
from . import prompts as P

BEAT_RE = re.compile(r"(?:E(\d+)\.)?B(\d+)", re.I)


PROGRESS = None   # optional callback(msg) - the Streamlit app sets this to show live progress


def log(msg: str):
    print(f"[promo-finder] {msg}", flush=True)
    if PROGRESS:
        try:
            PROGRESS(msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
def beat_ref(show: Show, ref: str, default_ep: int | None):
    m = BEAT_RE.search(str(ref or ""))
    if not m:
        return None
    ep = int(m.group(1)) if m.group(1) else default_ep
    if ep not in show.episodes:
        return None
    return show.episodes[ep].beat(f"E{ep:02d}.B{int(m.group(2)):03d}")


def promo_from_llm(show: Show, d: dict, pid: str, source: str, default_ep: int | None) -> tuple[Promo | None, str]:
    segs = []
    for s in d.get("segments") or []:
        a = beat_ref(show, s.get("start_beat"), default_ep)
        b = beat_ref(show, s.get("end_beat"), default_ep)
        if not a or not b:
            return None, f"unknown beat id in {s}"
        if a.episode != b.episode:
            return None, f"segment spans two episodes {s}"
        if b.start < a.start:
            a, b = b, a
        # the end of a segment is the end of the last *non-camera* beat (camera notes may run long)
        segs.append(Segment(a.episode, a.start, b.end))
    if not segs:
        return None, "no segments"
    sc = d.get("scores") or {}
    p = Promo(id=pid, segments=segs, title=str(d.get("title", ""))[:80], hook=str(d.get("hook", "")),
              cliffhanger=str(d.get("cliffhanger", "")), why=str(d.get("why", "")), source=source,
              llm_scores={k: _num(sc.get(k)) for k in ("hook", "cliffhanger", "standalone")},
              notes=str(d.get("needs_context", "")))
    return p, ""


def _num(x):
    try:
        return max(0.0, min(10.0, float(x)))
    except (TypeError, ValueError):
        return None


def hint_rows(show: Show, promos: list[Promo]) -> list[dict]:
    rows = []
    for p in promos:
        s = p.segments[0]
        bs = show.beats_in(s, ("dialogue", "action", "sfx", "camera"))
        if not bs:
            continue
        rows.append({"start_beat": bs[0].id, "end_beat": bs[-1].id, "start": sec_to_ts(s.start), "end": sec_to_ts(s.end),
                     "duration": s.duration, "score": p.score, "hook": p.hook, "cliff": p.cliffhanger})
    return rows


def highlights(show: Show, per_ep: int = 14) -> dict[int, list[str]]:
    out = {}
    for n, ep in show.episodes.items():
        bs = [b for b in ep.beats if b.kind in ("dialogue", "action", "sfx")]
        top = sorted(bs, key=lambda b: -max(show.sig[b.id]["hook"], show.sig[b.id]["cliff"]))[:per_ep]
        out[n] = [b.line() for b in sorted(top, key=lambda b: b.start)]
    return out


# ---------------------------------------------------------------------------
def composite(show: Show, p: Promo, n_eps: int) -> float:
    h = p.heur
    ls = p.llm_scores or {}

    def mix(llm_v, heur_v):
        return heur_v if llm_v is None else 0.7 * llm_v / 10 + 0.3 * heur_v

    hook = mix(ls.get("hook"), h["hook"])
    cliff = mix(ls.get("cliffhanger"), h["cliff"])
    stand = mix(ls.get("standalone"), h["standalone"])
    funnel = 1 - 0.6 * (min(p.episodes) - 1) / max(1, n_eps - 1)
    warn = set(p.checks.get("warnings") or [])
    fails = sum(not p.checks[k] for k in ("length_ok", "hook_in_3s", "ends_unresolved", "clean_cuts", "has_dialogue",
                                          "segments_ok") if k not in warn)
    s = 0.32 * hook + 0.30 * cliff + 0.20 * stand + 0.08 * h["intensity"] + 0.10 * funnel - 0.12 * fails - 0.04 * len(warn)
    return round(s, 4)


def dedupe(promos: list[Promo], thr: float = 0.5) -> list[Promo]:
    keep: list[Promo] = []
    for p in sorted(promos, key=lambda x: -x.score):
        if all(iou(p, q) < thr for q in keep):
            keep.append(p)
    return keep


def diverse_order(promos: list[Promo], k: int) -> list[Promo]:
    """Greedy: best score, penalise repeating an episode or overlapping screen time."""
    pool, out = list(promos), []
    while pool and len(out) < k:
        def adj(p):
            same = sum(1 for q in out if set(p.episodes) == set(q.episodes))
            return p.score - 0.05 * same - 0.4 * max((iou(p, q) for q in out), default=0)
        best = max(pool, key=adj)
        out.append(best)
        pool.remove(best)
    return out


# ---------------------------------------------------------------------------
def run(input_dir: str, out_dir: str = "outputs", provider: str = "none", model: str | None = None,
        base_url: str | None = None, api_key: str | None = None, show_name: str | None = None,
        top_n: int = 15, judge: bool = True, episodes: list[int] | None = None, redact: bool = False) -> dict:
    """redact=True leaves script transcripts out of promos.json / report.html (for public sharing)."""
    t0 = time.time()
    out = long_path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    llm = LLM(provider, model, out, base_url, api_key)
    llm.log = log

    # 1 parse ---------------------------------------------------------------
    eps = load_show(input_dir)
    if episodes:
        eps = [e for e in eps if e.number in set(episodes)]
    if not eps:
        raise SystemExit(f"no screenplays found in {input_dir}")
    show_name = show_name or Path(input_dir).resolve().name
    if show_name.lower() in ("data", "input", "scripts") and (Path(input_dir) / "").exists():
        subs = [p for p in long_path(input_dir).iterdir() if p.is_dir()]
        if len(subs) == 1:
            show_name = subs[0].name
    show = Show(eps, show_name)
    n_eps = len(eps)
    log(f"parsed {n_eps} episodes, {sum(len(e.beats) for e in eps)} beats, "
        f"{sum(e.duration for e in eps) / 60:.1f} min of screen time")
    _dump(out / "01_parsed_episodes.json", [{"episode": e.number, "logline": e.logline, "duration": round(e.duration, 2),
                                             "scenes": len(e.scenes), "beats": len(e.beats),
                                             "safe_cut_points": len(show.cuts(e.number))} for e in eps])

    # 2 heuristic shortlist -------------------------------------------------
    per_ep, stitched = generate(show)
    heur_all = [p for lst in per_ep.values() for p in lst] + stitched
    _dump(out / "02_heuristic_candidates.json", [p.to_dict() for p in heur_all])
    log(f"heuristic shortlist: {len(heur_all)} candidates ({len(stitched)} stitched)")

    rejected: list[dict] = []
    if not llm.enabled:
        pool = []
        for lst in per_ep.values():
            pool.extend(lst[:2])
        pool += stitched[:8]
    else:
        # 3 per-episode LLM picks ------------------------------------------------
        pool = []
        for k, e in enumerate(eps, 1):
            log(f"LLM pick: episode {e.number} ({k}/{n_eps})")
            hints = hint_rows(show, per_ep[e.number][:5])
            txt = llm.ask(f"pick_E{e.number:02d}", P.SYSTEM_EDITOR, P.episode_prompt(show_name, e, eps, hints))
            if txt is None:
                continue
            data = parse_json(txt)
            if not isinstance(data, dict):
                rejected.append({"stage": "pick", "episode": e.number, "reason": "unparseable JSON"})
                continue
            for i, d in enumerate(data.get("promos") or []):
                p, err = promo_from_llm(show, d, f"E{e.number:02d}-P{i + 1}", "llm", e.number)
                if not p:
                    rejected.append({"stage": "pick", "episode": e.number, "reason": err, "raw": d})
                    continue
                if any(s.episode != e.number for s in p.segments):
                    rejected.append({"stage": "pick", "episode": e.number, "reason": "segment outside episode", "raw": d})
                    continue
                pool.append(p)
        if llm.pending:
            _pending_exit(llm, out)
        log(f"LLM picked {len(pool)} single-episode promos ({len(rejected)} rejected)")

        # 4 cross-episode stitches ------------------------------------------------
        for p in pool:
            show.repair(p)
            show.validate(p)
        acc = [{"id": p.id, "episodes": p.episodes, "title": p.title, "hook": p.hook,
                "segments": [f"{sec_to_ts(s.start)}-{sec_to_ts(s.end)}" for s in p.segments]} for p in pool]
        log("LLM: stitching cross-episode promos")
        txt = llm.ask("stitch_all", P.SYSTEM_EDITOR, P.cross_prompt(show_name, eps, highlights(show), acc))
        if txt is None:
            _pending_exit(llm, out)
        data = parse_json(txt) or {}
        for i, d in enumerate(data.get("promos") or []):
            p, err = promo_from_llm(show, d, f"X-{i + 1:02d}", "llm-cross", None)
            if not p:
                rejected.append({"stage": "stitch", "reason": err, "raw": d})
                continue
            pool.append(p)

    # 5 repair + validate --------------------------------------------------------
    for p in pool:
        if p.source != "heuristic":
            show.repair(p)
        show.validate(p)
        if not p.hook:
            describe(show, p)
        p.score = composite(show, p, n_eps)
    pool = dedupe(pool)

    # 6 rank ----------------------------------------------------------------------
    passed = sorted([p for p in pool if p.checks["passed"]], key=lambda p: -p.score)
    failed = sorted([p for p in pool if not p.checks["passed"]], key=lambda p: -p.score)
    order = diverse_order(passed, top_n)
    strategy, weak = "", []
    if llm.enabled:
        shortlist = passed[:25]          # already de-duplicated; the model handles variety
        log("LLM: ranking the shortlist")
        txt = llm.ask("rank", P.SYSTEM_EDITOR, P.rank_prompt(show_name, n_eps, [p.to_dict() for p in shortlist]))
        if txt is None:
            _pending_exit(llm, out)
        data = parse_json(txt) or {}
        by_id = {p.id: p for p in shortlist}
        llm_order = []
        for row in data.get("order") or []:
            p = by_id.get(str(row.get("id")))
            if p and p not in llm_order:
                p.rank_reason = str(row.get("reason", ""))
                llm_order.append(p)
        strategy = str(data.get("strategy", ""))
        weak = data.get("weak") or []
        weak_ids = {str(w.get("id")) for w in weak}
        rest = [p for p in diverse_order(passed, len(passed)) if p not in llm_order and p.id not in weak_ids]
        order = (llm_order + rest)[:top_n]
    for i, p in enumerate(order):
        p.rank = i + 1
        if not p.rank_reason:
            p.rank_reason = (f"composite {p.score:.2f}: hook {p.heur['hook']:.2f}, cliff {p.heur['cliff']:.2f}, "
                             f"from E{min(p.episodes)}")

    # 7 blind judge -----------------------------------------------------------------
    if llm.enabled and judge:
        items = [{"id": p.id, "duration": p.duration,
                  "lines": [re.sub(r"^E\d+\.B\d+ ", "", ln) for ln in show.transcript(p)]} for p in order[:12]]
        log("LLM: blind-viewer QA")
        txt = llm.ask("judge", P.SYSTEM_JUDGE, P.judge_prompt(items))
        if txt is None:
            _pending_exit(llm, out)
        data = parse_json(txt) or {}
        by_id = {p.id: p for p in order}
        for c in data.get("clips") or []:
            if str(c.get("id")) in by_id:
                p = by_id[str(c["id"])]
                p.judge = c
                try:
                    if float(c.get("clear", 10)) <= 3:     # a stranger could not follow it: human must look
                        p.checks.setdefault("warnings", []).append("blind_viewer_confused")
                except (TypeError, ValueError):
                    pass

    # 8 write -----------------------------------------------------------------------
    ranked_ids = {p.id for p in order}
    others = [p for p in passed if p.id not in ranked_ids]
    result = {
        "show": show_name, "episodes": n_eps, "provider": llm.preset, "model": llm.model,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0, 1),
        "strategy": strategy, "weak": weak,
        "ranked": [p.to_dict() for p in order],
        "other_valid": [p.to_dict() for p in others],
        "failed_checks": [p.to_dict() for p in failed],
        "rejected_llm_output": rejected,
    }
    for p in order + others + failed:
        p_d = next(x for x in result["ranked"] + result["other_valid"] + result["failed_checks"] if x["id"] == p.id)
        p_d["transcript"] = ["(transcript omitted: run without --redact to include the script lines)"] if redact \
            else show.transcript(p)
    result["quality"] = quality(show, pool, order, heur_all, rejected, llm)
    _dump(out / "promos.json", result)
    write_csv(out / "promos_ranked.csv", order + others)
    from .report import write_markdown, write_html
    write_markdown(out / "promos.md", result)
    write_html(out / "report.html", result)
    log(f"done in {result['seconds']}s -> {out}")
    return result


def quality(show: Show, pool, order, heur_all, rejected, llm: LLM) -> dict:
    n = len(pool) or 1
    q = {
        "candidates_after_dedupe": len(pool),
        "pass_rate": round(sum(p.checks["passed"] for p in pool) / n, 3),
        "check_fail_counts": {k: sum(not p.checks[k] and k not in (p.checks.get("warnings") or []) for p in pool) for k in
                              ("length_ok", "hook_in_3s", "ends_unresolved", "clean_cuts", "has_dialogue", "segments_ok")},
        "review_warnings": {k: sum(k in (p.checks.get("warnings") or []) for p in pool)
                            for k in ("hook_in_3s", "ends_unresolved")},
        "ranked_with_warnings": sum(bool(p.checks.get("warnings")) for p in order),
        "auto_repaired": sum(bool(p.repairs) for p in pool),
        "repairs": {p.id: p.repairs for p in pool if p.repairs},
        "rejected_llm_items": len(rejected),
        "avg_duration_ranked": round(sum(p.duration for p in order) / max(1, len(order)), 1),
        "ranked_from_first_10_episodes": sum(min(p.episodes) <= 10 for p in order),
        "llm_usage": llm.usage,
    }
    if any(p.source != "heuristic" for p in pool):
        # agreement = LLM promo overlaps (IoU >= 0.3) one of the heuristic's own top-2 picks for that episode
        llm_p = [p for p in pool if p.source == "llm"]
        top2 = {}
        for h in heur_all:
            if len(h.segments) == 1:
                top2.setdefault(h.segments[0].episode, []).append(h)
        agree = [p for p in llm_p if any(iou(p, h) >= 0.3 for h in sorted(top2.get(p.episodes[0], []),
                                                                            key=lambda x: -x.score)[:2])]
        q["llm_vs_heuristic_top2_agreement"] = round(len(agree) / max(1, len(llm_p)), 3)
        q["blind_viewer_confused_in_ranked"] = sum("blind_viewer_confused" in (p.checks.get("warnings") or [])
                                                   for p in order)
        chars_in, chars_out = llm.usage["input_chars"], llm.usage["output_chars"]
        q["approx_tokens"] = {"input": chars_in // 4, "output": chars_out // 4}
        js = [p.judge for p in order if p.judge]
        if js:
            q["blind_judge"] = {k: round(sum(float(j.get(k, 0)) for j in js) / len(js), 2) for k in ("stop", "clear", "next")}
            q["blind_judge"]["would_click_rate"] = round(sum(bool(j.get("would_click")) for j in js) / len(js), 2)
    return q


def write_csv(path: Path, promos: list[Promo]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "id", "source", "episodes", "segments", "duration_s", "title", "hook", "cliffhanger", "why",
                    "score", "checks_passed", "hook_strength", "cliff_strength", "repairs"])
        for p in promos:
            w.writerow([p.rank or "", p.id, p.source, " + ".join(f"E{e}" for e in p.episodes),
                        " | ".join(f"E{s.episode} {sec_to_ts(s.start)}-{sec_to_ts(s.end)}" for s in p.segments),
                        p.duration, p.title, p.hook, p.cliffhanger, p.why, p.score, p.checks.get("passed"),
                        p.checks.get("hook_strength"), p.checks.get("cliff_strength"), "; ".join(p.repairs)])


def _dump(path: Path, obj):
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


def _pending_exit(llm: LLM, out: Path):
    msg = (f"{len(llm.pending)} prompt(s) need an answer (manual provider). Paste each file in "
           f"{out / 'llm' / 'pending'} into any chat model, save the reply next to it in {out / 'llm'} as "
           f"<name>.response.txt, then re-run the same command.")
    log(msg)
    raise PendingPrompts(msg)
