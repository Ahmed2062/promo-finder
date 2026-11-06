"""Heuristic candidate generation (stage 1, no LLM, ~1 s for a 50-episode show).

* single-stretch windows: every pair of safe cut points 20-60 s apart, scored
  on hook-in-3s, unresolved ending, intensity, standalone-ness and length.
* stitched "cold open" promos: a 4-12 s hook burst + a 15-45 s stretch that ends
  on a cliffhanger, from the same episode or up to 3 episodes later.

The output doubles as (a) hints for the LLM, and (b) a usable fallback answer
when no model is configured.
"""
from __future__ import annotations

from .core import Show, Promo, Segment, iou, MIN_LEN, MAX_LEN
from .parser import sec_to_ts


def _dur_pref(d: float) -> float:
    if 25 <= d <= 45:
        return 1.0
    if d < 25:
        return max(0.0, 0.5 + 0.5 * (d - MIN_LEN) / 5)
    return max(0.0, 1.0 - 0.5 * (d - 45) / 15)


def heuristic_score(show: Show, p: Promo) -> float:
    show.validate(p)
    h = p.heur
    s = 0.34 * h["hook"] + 0.30 * h["cliff"] + 0.16 * h["intensity"] + 0.10 * h["standalone"] \
        + 0.10 * _dur_pref(p.duration)
    if not p.checks["clean_cuts"]:
        s -= 0.2
    if not p.checks["has_dialogue"]:
        s -= 0.15
    return round(s, 4)


def describe(show: Show, p: Promo) -> None:
    """Template hook / cliffhanger / why text for heuristic promos."""
    hb = show.episodes[p.segments[0].episode].beat(p.checks.get("hook_beat") or "")
    cb = show.episodes[p.segments[-1].episode].beat(p.checks.get("cliff_beat") or "")
    fl = show.first_line(p, ("dialogue", "action"))
    hook_b = hb or fl
    p.hook = (f"{hook_b.speaker.title()}: \"{hook_b.text}\"" if hook_b and hook_b.kind == "dialogue"
              else (hook_b.text if hook_b else ""))
    ll = cb or show.last_line(p, ("dialogue", "action", "sfx"))
    p.cliffhanger = (f"{ll.speaker.title()}: \"{ll.text}\"" if ll and ll.kind == "dialogue"
                     else (ll.text if ll else ""))
    hw = show.sig[hook_b.id]["hook_hits"][:3] if hook_b else []
    cw = show.sig[ll.id]["cliff_hits"][:3] if ll else []
    p.why = (f"Opens on a high-intensity beat ({', '.join(hw) or 'strong emotion'}) and cuts on an unresolved "
             f"moment ({', '.join(cw) or 'episode-ending beat'}).")
    p.title = f"E{p.segments[0].episode:02d} {sec_to_ts(p.segments[0].start)}" + (
        " (stitched)" if len(p.segments) > 1 else "")


def _fast_window_scores(show: Show, ep_no: int) -> list[tuple[float, float, float]]:
    """Vectorised-ish version of heuristic_score for every (start, end) cut pair.
    Same formula as heuristic_score, computed with bisect + prefix sums so a
    50-episode show scores ~400k windows in seconds."""
    from bisect import bisect_left
    from .core import TOL, HOOK_WINDOW
    from .signals import starts_mid_context
    ep = show.episodes[ep_no]
    beats = ep.beats
    starts = [b.start for b in beats]
    hook = [show.sig[b.id]["hook"] for b in beats]
    cliff = [show.sig[b.id]["cliff"] * (1.15 if b.kind == "dialogue" else 1.0) for b in beats]
    is_d = [b.kind == "dialogue" for b in beats]
    pref = [0]
    for x in is_d:
        pref.append(pref[-1] + x)
    nxt_d = [None] * (len(beats) + 1)
    for i in range(len(beats) - 1, -1, -1):
        nxt_d[i] = i if is_d[i] else nxt_d[i + 1]
    mid_ctx = [starts_mid_context(b.text) if is_d[i] else False for i, b in enumerate(beats)]
    cuts = show.cuts(ep_no)
    out = []
    for i, a in enumerate(cuts):
        lo = bisect_left(starts, a - TOL)
        h_hi = bisect_left(starts, a + HOOK_WINDOW + 1e-6)
        h3 = max(hook[lo:h_hi], default=0.0)
        for b in cuts[i + 1:]:
            d = b - a
            if d < MIN_LEN:
                continue
            if d > MAX_LEN:
                break
            hi = bisect_left(starts, b - TOL)
            if hi <= lo:
                continue
            c, resolved = 0.0, False
            for k in range(hi - 1, lo - 1, -1):
                bt = beats[k]
                if b - bt.end <= 5.0 or (is_d[k] and b - bt.start <= 6.0):
                    c = max(c, min(1.0, cliff[k]))
                    resolved |= show.sig[bt.id]["resolved"]
                elif b - bt.start > 12:
                    break
            if abs(b - ep.duration) < 1.0 and not resolved:
                c = max(c, 0.75)
            top = sorted(hook[lo:hi], reverse=True)[:6]
            inten = sum(top) / 6
            n_lines = pref[hi] - pref[lo]
            fd = nxt_d[lo]
            st = 1.0
            if fd is None or fd >= hi:
                st = 0.2
            else:
                if mid_ctx[fd]:
                    st -= 0.35
                if n_lines < 4:
                    st -= 0.3
            s = 0.34 * h3 + 0.30 * c + 0.16 * inten + 0.10 * max(0.0, st) + 0.10 * _dur_pref(d)
            if n_lines < 3:
                s -= 0.15
            out.append((round(s, 4), a, b))
    return out


def single_windows(show: Show, ep_no: int, top_k: int = 6) -> list[Promo]:
    scored = sorted(_fast_window_scores(show, ep_no), reverse=True)
    keep: list[Promo] = []
    for s, a, b in scored:
        p = Promo(id="", segments=[Segment(ep_no, a, b)])
        if all(iou(p, q) < 0.35 for q in keep):
            p.score = heuristic_score(show, p)   # exact score + checks for the survivors
            keep.append(p)
        if len(keep) >= top_k:
            break
    return sorted(keep, key=lambda p: -p.score)


def nms(cands: list[Promo], top_k: int, thr: float = 0.35) -> list[Promo]:
    cands = sorted(cands, key=lambda p: -p.score)
    keep: list[Promo] = []
    for p in cands:
        if all(iou(p, q) < thr for q in keep):
            keep.append(p)
        if len(keep) >= top_k:
            break
    return keep


def stitched(show: Show, per_ep_top: dict[int, list[Promo]], max_gap: int = 3, top_k: int = 12) -> list[Promo]:
    """Hook burst from episode i + cliffhanger stretch from episode i..i+max_gap."""
    bursts: list[tuple[float, Segment]] = []
    for ep_no in show.episodes:
        cuts = show.cuts(ep_no)
        best = None
        for i, a in enumerate(cuts):
            for b in cuts[i + 1:]:
                if b - a < 4:
                    continue
                if b - a > 12:
                    break
                p = Promo(id="", segments=[Segment(ep_no, a, b)])
                h, _ = show.hook_strength(p)
                v = h + 0.3 * show.intensity(p)
                if not best or v > best[0]:
                    best = (v, Segment(ep_no, a, b))
        if best:
            bursts.append(best)
    out: list[Promo] = []
    for v, burst in bursts:
        for j in range(burst.episode, burst.episode + max_gap + 1):
            for tail in per_ep_top.get(j, [])[:3]:
                t = tail.segments[-1]
                if t.episode == burst.episode and t.start < burst.end + 2:
                    continue
                remaining = MAX_LEN - burst.duration
                start = t.start
                if t.end - start > remaining:
                    start = show.snap_end(t.episode, t.end - remaining)
                if t.end - start < 12:
                    continue
                p = Promo(id="", segments=[burst, Segment(t.episode, start, t.end)])
                if p.duration < MIN_LEN:
                    continue
                p.score = heuristic_score(show, p) - 0.02 * (j - burst.episode)
                out.append(p)
    return nms(out, top_k, thr=0.25)


def generate(show: Show, per_episode: int = 6) -> tuple[dict[int, list[Promo]], list[Promo]]:
    per_ep = {n: single_windows(show, n, per_episode) for n in show.episodes}
    st = stitched(show, per_ep)
    k = 0
    for n, lst in per_ep.items():
        for p in lst:
            k += 1
            p.id = f"H{n:02d}-{k:03d}"
            describe(show, p)
    for i, p in enumerate(st):
        p.id = f"HX-{i + 1:02d}"
        describe(show, p)
    return per_ep, st
