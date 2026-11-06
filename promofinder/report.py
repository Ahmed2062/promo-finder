"""Human-facing outputs: promos.md and a self-contained report.html (no CDN, opens offline)."""
from __future__ import annotations

import html
import json
from pathlib import Path


def _segs(p: dict) -> str:
    return " + ".join(f"E{s['episode']} {s['start']}–{s['end']}" for s in p["segments"])


def write_markdown(path: Path, r: dict):
    L = [f"# Promos — {r['show']}", "",
         f"{r['episodes']} episodes · provider `{r['provider']}` / `{r['model']}` · generated {r['generated_at']}", ""]
    if r.get("strategy"):
        L += ["**Posting plan:** " + r["strategy"], ""]
    L += ["## Ranked (post in this order)", ""]
    for p in r["ranked"]:
        L += [f"### {p['rank']}. {p['title']}  ·  {p['duration']:.0f}s  ·  {p['source']}",
              f"- **Segments:** {_segs(p)}",
              f"- **Hook:** {p['hook']}",
              f"- **Cliffhanger:** {p['cliffhanger']}",
              f"- **Why it works:** {p['why']}",
              f"- **Why this slot:** {p['rank_reason']}",
              f"- **Checks:** hook {p['checks']['hook_strength']:.2f} · cliff {p['checks']['cliff_strength']:.2f} · "
              f"score {p['score']:.2f}" + (f" · repaired: {'; '.join(p['repairs'])}" if p["repairs"] else "")]
        if p.get("judge"):
            j = p["judge"]
            L.append(f"- **Blind viewer:** stop {j.get('stop')}/10 · clear {j.get('clear')}/10 · next {j.get('next')}/10 · "
                     f"confusing: {j.get('confusing')}")
        L.append("")
    L += ["## Other valid promos", ""]
    for p in r["other_valid"]:
        L.append(f"- {p['id']} · {_segs(p)} · {p['duration']:.0f}s · {p['title']} — {p['hook'][:80]}")
    L += ["", "## Failed automatic checks (not ranked)", ""]
    for p in r["failed_checks"]:
        fails = [k for k in ("length_ok", "hook_in_3s", "ends_unresolved", "clean_cuts", "has_dialogue", "segments_ok")
                 if not p["checks"][k]]
        L.append(f"- {p['id']} · {_segs(p)} · {p['duration']:.0f}s · failed: {', '.join(fails)}")
    if r.get("weak"):
        L += ["", "## Flagged by the ranking model", ""]
        L += [f"- {w.get('id')}: {w.get('problem')}" for w in r["weak"]]
    L += ["", "## Quality", "", "```json", json.dumps(r["quality"], indent=1, ensure_ascii=False)[:6000], "```"]
    path.write_text("\n".join(L), encoding="utf-8")


CSS = """
:root{--bg:#f7f5f2;--card:#fff;--ink:#1d1b19;--muted:#6b655e;--line:#e7e1d9;--accent:#c2410c;--accent2:#7c3aed;
--ok:#15803d;--bad:#b91c1c;--bar:#efe9e1;--seg:#c2410c;--seg2:#7c3aed}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141210;--card:#1d1a17;--ink:#f1ece6;--muted:#a59d93;
--line:#2f2a25;--accent:#fb923c;--accent2:#a78bfa;--ok:#4ade80;--bad:#f87171;--bar:#2a2520;--seg:#fb923c;--seg2:#a78bfa}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Inter,Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:28px 16px 80px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}h2{font-size:18px;margin:36px 0 12px}
.sub{color:var(--muted);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--muted);font-size:12px}
.plan{background:var(--card);border-left:4px solid var(--accent);border-radius:8px;padding:12px 16px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:12px 0}
.top{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
.rank{font-size:28px;font-weight:700;color:var(--accent);min-width:34px}
.title{font-weight:650;font-size:17px;flex:1}
.pill{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--muted);white-space:nowrap}
.pill.x{color:var(--accent2);border-color:var(--accent2)}
.grid{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;margin-top:10px;font-size:14px}
.grid dt{color:var(--muted)}.grid dd{margin:0}
.tl{margin:10px 0 2px}.tlrow{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted);margin:3px 0}
.tlbar{position:relative;flex:1;height:10px;background:var(--bar);border-radius:5px}
.tlseg{position:absolute;top:0;height:10px;border-radius:5px;background:var(--seg)}
.checks{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.ck{font-size:12px;padding:2px 8px;border-radius:6px;background:var(--bar)}
.ck.ok{color:var(--ok)}.ck.bad{color:var(--bad)}
details{margin-top:10px}summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;background:var(--bar);padding:10px;border-radius:8px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:6px 4px;text-align:left;vertical-align:top}
.judge{font-size:13px;color:var(--muted);margin-top:8px}
@media(max-width:600px){.grid{grid-template-columns:1fr}.grid dt{margin-top:6px}}
"""


def _e(s) -> str:
    return html.escape(str(s or ""))


def _timeline(p: dict, durations: dict[int, float]) -> str:
    rows = []
    for i, s in enumerate(p["segments"]):
        d = durations.get(s["episode"], 1) or 1
        left = 100 * s["start_sec"] / d
        width = max(1.2, 100 * (s["end_sec"] - s["start_sec"]) / d)
        rows.append(f'<div class="tlrow"><span style="width:44px">E{s["episode"]:02d}</span><div class="tlbar">'
                    f'<div class="tlseg" style="left:{left:.1f}%;width:{width:.1f}%;{"background:var(--seg2)" if i else ""}"></div>'
                    f'</div><span style="width:120px">{s["start"]}–{s["end"]}</span></div>')
    return '<div class="tl">' + "".join(rows) + "</div>"


def _card(p: dict, durations: dict[int, float], ranked=True) -> str:
    c = p["checks"]
    warn = set(c.get("warnings") or [])
    marks = [("20–60s", c["length_ok"], "length_ok"), ("hook ≤3s", c["hook_in_3s"], "hook_in_3s"),
             ("ends unresolved", c["ends_unresolved"], "ends_unresolved"), ("clean cuts", c["clean_cuts"], "clean_cuts"),
             ("has dialogue", c["has_dialogue"], "has_dialogue")]
    ck = "".join(
        f'<span class="ck {"ok" if v else ("" if key in warn else "bad")}">'
        f'{"✓" if v else ("⚠" if key in warn else "✗")} {k}{" — review" if key in warn else ""}</span>'
        for k, v, key in marks)
    if "blind_viewer_confused" in warn:
        ck += '<span class="ck bad">⚠ blind viewer confused — review</span>'
    ck += f'<span class="ck">hook {c["hook_strength"]:.2f}</span><span class="ck">cliff {c["cliff_strength"]:.2f}</span>'
    ls = p.get("llm_scores") or {}
    if ls.get("hook") is not None:
        ck += f'<span class="ck">model h/c/s {ls.get("hook"):.0f}/{ls.get("cliffhanger") or 0:.0f}/{ls.get("standalone") or 0:.0f}</span>'
    j = p.get("judge") or {}
    judge = (f'<div class="judge">Blind viewer (no context): stop {_e(j.get("stop"))}/10 · clear {_e(j.get("clear"))}/10 · '
             f'next {_e(j.get("next"))}/10 · {"would click" if j.get("would_click") else "would not click"} · '
             f'confusing: {_e(j.get("confusing"))}</div>') if j else ""
    rep = f"<dt>Auto-repairs</dt><dd>{_e('; '.join(p['repairs']))}</dd>" if p.get("repairs") else ""
    slot = f"<dt>Why this slot</dt><dd>{_e(p['rank_reason'])}</dd>" if ranked and p.get("rank_reason") else ""
    ctx = f"<dt>Needs context</dt><dd>{_e(p['notes'])}</dd>" if p.get("notes") and p["notes"].lower() != "nothing" else ""
    return f"""<div class="card"><div class="top"><span class="rank">{_e(p.get('rank') or '·')}</span>
<span class="title">{_e(p['title'])}</span><span class="pill {'x' if len(p['episodes']) > 1 else ''}">{'stitched · ' if len(p['segments']) > 1 else ''}{p['duration']:.0f}s</span>
<span class="pill">{_e(p['id'])} · {_e(p['source'])}</span></div>
{_timeline(p, durations)}
<dl class="grid"><dt>Segments</dt><dd>{_e(_segs(p))}</dd><dt>Hook</dt><dd>{_e(p['hook'])}</dd>
<dt>Cliffhanger</dt><dd>{_e(p['cliffhanger'])}</dd><dt>Why it works</dt><dd>{_e(p['why'])}</dd>{slot}{ctx}{rep}</dl>
<div class="checks">{ck}</div>{judge}
<details><summary>Transcript of the cut</summary><pre>{_e(chr(10).join(p.get('transcript', [])))}</pre></details></div>"""


def write_html(path: Path, r: dict):
    durations = {}
    for p in r["ranked"] + r["other_valid"] + r["failed_checks"]:
        for s in p["segments"]:
            durations[s["episode"]] = max(durations.get(s["episode"], 0), s["end_sec"])
    try:
        meta = json.loads((path.parent / "01_parsed_episodes.json").read_text(encoding="utf-8"))
        durations.update({m["episode"]: m["duration"] for m in meta})
    except Exception:
        pass
    q = r["quality"]
    bj = q.get("blind_judge") or {}
    kpis = [(len(r["ranked"]), "ranked promos"), (q["candidates_after_dedupe"], "candidates checked"),
            (f"{q['pass_rate'] * 100:.0f}%", "passed all checks"), (q["auto_repaired"], "auto-repaired"),
            (f"{q['avg_duration_ranked']}s", "avg length (ranked)")]
    if bj:
        kpis.append((f"{bj.get('stop')}/10", "blind-viewer stop score"))
    k = "".join(f'<div class="kpi"><b>{_e(a)}</b><span>{_e(b)}</span></div>' for a, b in kpis)
    plan = f'<div class="plan"><b>Posting plan.</b> {_e(r["strategy"])}</div>' if r.get("strategy") else ""
    ranked = "".join(_card(p, durations) for p in r["ranked"])
    others = "".join(_card(p, durations, False) for p in r["other_valid"][:30])
    fails = "".join(
        f"<tr><td>{_e(p['id'])}</td><td>{_e(_segs(p))}</td><td>{p['duration']:.0f}s</td><td>"
        + ", ".join(k for k in ("length_ok", "hook_in_3s", "ends_unresolved", "clean_cuts", "has_dialogue", "segments_ok")
                    if not p["checks"][k]) + f"</td><td>{_e(p['title'])}</td></tr>" for p in r["failed_checks"])
    weak = "".join(f"<tr><td>{_e(w.get('id'))}</td><td>{_e(w.get('problem'))}</td></tr>" for w in r.get("weak") or [])
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Promo Finder Report</title><style>{CSS}</style></head><body><div class="wrap">
<h1>Promo Finder — {_e(r['show'])}</h1>
<div class="sub">{r['episodes']} episodes · provider {_e(r['provider'])} / {_e(r['model'])} · generated {_e(r['generated_at'])} · {r['seconds']}s</div>
<div class="kpis">{k}</div>{plan}
<h2>Post these first</h2>{ranked}
<h2>Other valid promos</h2>{others or '<p class="sub">none</p>'}
<h2>Rejected by automatic checks</h2><table><tr><th>id</th><th>segments</th><th>len</th><th>failed</th><th>title</th></tr>{fails}</table>
{('<h2>Flagged by the ranking model</h2><table>' + weak + '</table>') if weak else ''}
<h2>Quality numbers</h2><pre>{_e(json.dumps({k: v for k, v in q.items() if k != 'repairs'}, indent=1, ensure_ascii=False))}</pre>
</div></body></html>"""
    path.write_text(doc, encoding="utf-8")
