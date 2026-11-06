"""Build the 2-page solution doc (docs/solution.pdf) from the real run outputs.

    python docs/build_doc.py
"""
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
R = json.loads((ROOT / "outputs/llm/promos.json").read_text(encoding="utf-8"))
H = json.loads((ROOT / "outputs/heuristic/promos.json").read_text(encoding="utf-8"))
Q, HQ = R["quality"], H["quality"]

ACC = colors.HexColor("#c2410c")
INK = colors.HexColor("#1d1b19")
MUT = colors.HexColor("#6b655e")
base = ParagraphStyle("b", fontName="Helvetica", fontSize=8.3, leading=10.4, textColor=INK, alignment=TA_LEFT,
                      spaceAfter=2.2)
h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=14, leading=17, spaceAfter=1)
sub = ParagraphStyle("s", parent=base, textColor=MUT, fontSize=8, spaceAfter=4)
h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=9.6, leading=12, textColor=ACC,
                    spaceBefore=4, spaceAfter=1.5)
bul = ParagraphStyle("bul", parent=base, leftIndent=8, bulletIndent=0, spaceAfter=1.2)
cell = ParagraphStyle("c", parent=base, fontSize=7.3, leading=8.8, spaceAfter=0)
cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")


def P(t, s=base):
    return Paragraph(t, s)


def B(items):
    return [Paragraph(t, bul, bulletText="•") for t in items]


def segs(p):
    return " + ".join(f"E{s['episode']} {s['start'][:-1]}–{s['end'][:-1]}" for s in p["segments"])


bj = Q.get("blind_judge", {})
tok_in, tok_out = Q["approx_tokens"]["input"], Q["approx_tokens"]["output"]


def cost(pin, pout):
    return tok_in / 1e6 * pin + tok_out / 1e6 * pout


story = [
    P("Promo Finder: picking microdrama promos from the screenplay", h1),
    P("Kuku AI Generalist assignment · Problem 1 · ran on all 50 episodes of <i>I Married a Broken Soldier. He was a Duke</i> "
      f"({R['episodes']} episodes, 103 min, 3,727 timestamped beats)", sub),
    P("<b>Prototype:</b> <link href='https://promo-finder-ahmed2062.streamlit.app' color='#2563eb'>promo-finder-ahmed2062.streamlit.app</link>"
      " · <b>Code + raw outputs:</b> <link href='https://github.com/Ahmed2062/promo-finder' color='#2563eb'>github.com/Ahmed2062/promo-finder</link>"
      " · full unredacted outputs shared privately as a zip", sub),

    P("1. Why this problem, and how I scoped it", h2),
    P("Promo selection is a weekly job that blocks every launch. It has a clear spec (20–60 s, hook in 3 s, cut on a cliffhanger, "
      "stands alone), and you can check it from the script alone, so an automated pass can do the first 90% and an editor the "
      "last 10%. I scoped it to <b>finding and ranking cuts</b>: no editing, music or overlays, as the brief says."),
    P("<b>Assumptions.</b>"),
    *B([
        "Script timestamps match the final cut closely enough to hand an editor a starting in/out point. They won't be "
        "frame-accurate, so the editor still trims.",
        "A clicked ad lands the viewer on episode 1, so promos from early episodes are worth more. The ranking gives a "
        "small bonus for that.",
        "Promos may spoil early twists but not the series' central reveal (the husband is the Duke).",
        "A promo can join 2–3 moments, from one episode or several.",
    ]),

    P("2. How it works: the LLM points, code measures", h2),
    *B([
        "<b>Parse (code).</b> A forgiving parser turns each script into beats with stable ids (<font face='Courier'>E12.B041 "
        "[01:03.10-01:05.40] CLARISSA (Cold): …</font>). From these it computes <b>safe cut points</b>, where nobody is "
        "mid-sentence.",
        "<b>Shortlist (code, free, 6 s).</b> Keyword and structure scores per beat: hook (violence, humiliation, reveals, "
        "emotion tags), cliffhanger (questions, threats, ultimatums, interruptions) and resolution (hugs, smiles, resolving "
        "music). It scores every 20–60 s window and builds stitched hook + payoff combos. This is the LLM's hint list and "
        "also the no-key fallback.",
        "<b>Pick (LLM, 1 call per episode).</b> The model gets the story so far (loglines only), the episode as numbered "
        "beats and the shortlist. It answers with <b>beat ids, never timestamps</b>, plus hook, cliffhanger, why it works "
        "and 0–10 scores. One more call builds cross-episode promos.",
        "<b>Repair + check (code).</b> Cuts snap to safe points. A promo over 60 s becomes hook (≈8 s) + payoff, as an "
        "editor would cut it; one under 20 s starts earlier. Hard rules: length, clean cuts, at least 3 dialogue lines, no "
        "tiny segments. The keyword hook and cliffhanger checks are hard for heuristic picks but only <b>review flags</b> "
        "for LLM picks, because in my audit the keyword checker was usually the one that was wrong.",
        "<b>Rank (code + 1 LLM call).</b> Score = 0.32 hook + 0.30 cliff + 0.20 standalone + 0.08 intensity + 0.10 funnel "
        "− penalties. Hook, cliff and standalone each blend 70% model score with 30% heuristic score. The LLM then sets "
        "the posting order from the top 25, gives a reason for each slot and flags weak or duplicate picks.",
        "<b>Blind viewer (1 LLM call, QA only).</b> A separate prompt sees only the clip's lines, with no show context, "
        "and rates stop / clear / next. A clarity score of 3 or below flags the promo for a human.",
    ]),
    P("<b>Human in the loop:</b> the editor gets <font face='Courier'>report.html</font>, a ranked list showing each cut on "
      "a timeline with its checks, review flags and transcript. They approve or adjust instead of watching 100 minutes. "
      "<b>Model swap:</b> <font face='Courier'>--provider anthropic|gemini|groq|openrouter|ollama|openai|manual|none</font> "
      "plus <font face='Courier'>--model</font>. Every prompt and raw reply is saved and cached, so reruns are free and "
      "reproducible."),
]

top = R["ranked"][:6]
rows = [[P("#", cellb), P("Segments (episode, mm:ss)", cellb), P("Hook → cliffhanger", cellb), P("Why post it here", cellb)]]
for p in top:
    rows.append([P(str(p["rank"]), cell), P(segs(p) + f"<br/>({p['duration']:.0f}s)", cell),
                 P(f"{p['hook'][:110]} → <i>{p['cliffhanger'][:100]}</i>", cell), P(p["rank_reason"][:190], cell)])
t = Table(rows, colWidths=[6 * mm, 40 * mm, 66 * mm, 68 * mm])
t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e7e1d9")),
                       ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3eee8")), ("TOPPADDING", (0, 0), (-1, -1), 2),
                       ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
story += [
    P("3. Output: what I would post first", h2),
    P(f"The model returned picks for 46 of 50 episodes and skipped four with no hook (E06, E08, E17, E40). That gave "
      f"{Q['candidates_after_dedupe']} promos after de-duplication, of which {len(R['ranked'])} are ranked. "
      + R["strategy"]),
    t, Spacer(1, 3),
    P("4. How I know it's good, and where it picks badly", h2),
    *B([
        f"<b>Rule checks:</b> {Q['pass_rate'] * 100:.0f}% of LLM promos pass every hard rule. 0 have bad lengths or "
        f"mid-line cuts; that is guaranteed, because code owns the timestamps. {Q['auto_repaired']} needed a sub-second "
        "snap to a clean cut. 0 LLM items were rejected for invalid beat ids.",
        f"<b>Model vs. heuristics:</b> {Q['llm_vs_heuristic_top2_agreement'] * 100:.0f}% of LLM picks overlap one of the "
        "heuristic's top-2 windows for that episode, so the two methods agree on <i>where</i> the moments are. They "
        f"disagree on hook and cliff quality: {Q['review_warnings']['hook_in_3s']} hook and "
        f"{Q['review_warnings']['ends_unresolved']} cliffhanger flags. I read 10 of them by hand: in about 7 the keywords "
        "were wrong (strong lines like \"Take her.\" and \"Your Grace.\" contain no keywords), and in about 3 the flag was "
        "right (E27 opens on a flat \"It is an antidote.\").",
        f"<b>Blind viewer:</b> stop {bj.get('stop')}/10, clear {bj.get('clear')}/10, next {bj.get('next')}/10. Only "
        f"{bj.get('would_click_rate', 0) * 100:.0f}% would click. That is harsh, but it is the most useful signal here, "
        "and it disagrees with the ranker where it should.",
    ]),
    P("<b>Where it fails</b>"),
    *B([
        "<b>Stitched cross-episode promos lose the story.</b> X-02 (mud + ledger → \"he has the late Duke's scar\") "
        "scored 7/10 standalone from the picking model but 2/10 clarity from the blind viewer. X-01's stranger proposes "
        "\"out of nowhere\" because the cut drops his entrance. Fix: require a character-intro beat in every stitched promo.",
        "<b>Romance and quiet beats don't read as text.</b> E10's near-kiss scored 3/10 stop. The tool can't see acting, "
        "chemistry or visuals, which are what sell these. It needs a video pass (frames + audio) to judge them.",
        "<b>The script names the same character several ways</b> (BEARDED STRANGER / MYSTERIOUS BEARDED MAN / ALEXANDER). "
        "That confuses standalone judgement; a character-alias pass would fix it.",
        "<b>The heuristics trust the script's own notes</b> (\"cliffhanger swell\"). They counted a \"resolving chord\" "
        "as a cliffhanger until I added resolution words, and keyword lists will always miss understated lines.",
        "<b>Late episodes spoil the main reveal.</b> The ranker flagged E42 and E43 for this, and the funnel bonus pushes "
        f"them down; {Q['ranked_from_first_10_episodes']} of the {len(R['ranked'])} ranked promos come from E1–E10.",
    ]),
    P("5. Scale and cost", h2),
    P(f"One show is about {tok_in / 1000:.0f}K input and {tok_out / 1000:.0f}K output tokens over 53 calls. At list prices "
      f"that's ≈${cost(2, 10):.2f} on Claude Sonnet 5.5, ≈${cost(4, 20):.2f} on Opus 5.5 and ≈${cost(0.10, 0.50):.2f} on "
      f"Haiku 5.5; free tiers (Gemini, Groq) or Ollama cost $0. The code stages take under 15 s. Per-episode calls run "
      "in parallel or through a batch API, and the story-so-far prefix can be cached. A new show every week is a "
      "one-command job plus about 20 minutes of editor review instead of 2+ hours of watching. Next steps: align the "
      "script to the actual video (ASR) for frame-accurate cuts, and learn the ranking weights from real promo "
      "CTR/CPI once posts are live."),
    P("6. AI tools, and one place they got it wrong", h2),
    P("I used Claude Code (Claude Opus 5.5) to design and write the code, and Claude as the LLM inside the pipeline. I had no "
      "free API key on this machine, so I ran the LLM stages through the tool's <font face='Courier'>manual</font> "
      "provider: each saved prompt was answered verbatim by a separate Claude agent that saw only that prompt. All raw "
      "prompts and replies are in <font face='Courier'>outputs/llm/llm/</font>, and any API provider reruns them with "
      "one flag. <b>Where AI got it wrong:</b> the picking model rated the stitched X-02 promo 7/10 for standing alone "
      "and the ranker put it 8th, but a context-free reader found it incoherent. Also, the heuristic Claude first wrote "
      "for me treated every episode ending as a cliffhanger, even calm, resolved ones, until the baseline run showed a "
      "\"resolving chord\" ranked 9th."),
]

out = ROOT / "docs" / "solution.pdf"
doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=11 * mm,
                        bottomMargin=10 * mm, title="Promo Finder - solution doc")
doc.build(story)
print("wrote", out)
