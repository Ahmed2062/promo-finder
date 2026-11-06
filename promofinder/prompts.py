"""Prompt templates. Kept in one file so they are easy to read, diff and tune."""
from __future__ import annotations

from .parser import Episode, sec_to_ts

SYSTEM_EDITOR = """You are a senior promo editor for vertical microdrama apps (ReelShort / DramaBox style).
You pick 20-60 second clips from a show that will run as paid and organic ads on TikTok, Reels and
YouTube Shorts to pull in NEW viewers who have never seen the show.

A great promo:
1. HOOK - the first 3 seconds stop a scrolling thumb: a slap, a public humiliation, a shocking line,
   a reveal, a threat, a kiss. Never open on an establishing shot, a calm line or a reaction to something we did not see.
2. CLIFFHANGER - it ends at an unresolved moment: a question hanging, a threat, an ultimatum, a door
   bursting open, a reveal about to land. Cut BEFORE the answer or the resolution. Never end on a hug,
   a resolved argument or a calm walk-away.
3. STANDS ALONE - a stranger understands who is wronged, by whom, and what is at stake from the clip
   alone. Archetypes (jilted bride, cruel sister, mysterious scarred husband, evil stepmother) carry it.
4. One continuous stretch is ideal. You may join 2-3 moments (e.g. a 5-second hook + the stretch
   that pays it off) when that makes a tighter promo.

You are precise and honest. You only reference beat ids that appear in the script. You reply with JSON only."""


def _script_lines(ep: Episode, cam_chars: int = 110) -> str:
    out = []
    cur_scene = None
    for b in ep.beats:
        if b.scene != cur_scene:
            cur_scene = b.scene
            sc = next((s for s in ep.scenes if s.number == b.scene), None)
            out.append(f"## SCENE {b.scene}: {sc.heading if sc else ''}")
        line = b.line()
        if b.kind == "camera" and len(line) > cam_chars + 30:
            line = line[: cam_chars + 30] + "…"
        out.append(line)
    return "\n".join(out)


def story_so_far(episodes: list[Episode], upto: int, max_eps: int = 60) -> str:
    prev = [e for e in episodes if e.number < upto][-max_eps:]
    return "\n".join(f"E{e.number:02d}: {e.logline}" for e in prev) or "(this is the first episode)"


def episode_prompt(show_name: str, ep: Episode, episodes: list[Episode], hints: list[dict]) -> str:
    hint_txt = "\n".join(
        f"- {h['start_beat']} -> {h['end_beat']} ({h['start']}-{h['end']}, {h['duration']:.0f}s) "
        f"auto-score {h['score']:.2f}: opens \"{h['hook'][:90]}\" / ends \"{h['cliff'][:90]}\""
        for h in hints) or "(none)"
    return f"""SHOW: {show_name}
TASK: find the best promos inside EPISODE {ep.number}.

STORY SO FAR (one line per earlier episode - the promo viewer has NOT seen any of this):
{story_so_far(episodes, ep.number)}

THIS EPISODE: {ep.logline}
Episode length: {sec_to_ts(ep.duration)}

SCRIPT (each line: beat_id [start-end] WHO: text. CAM = camera/visual note, ACTION = on-screen action):
{_script_lines(ep)}

AUTOMATIC SHORTLIST (keyword heuristics - use, adjust or ignore):
{hint_txt}

INSTRUCTIONS
- Return 0 to 3 promos for this episode, best first. Quality over quantity: if nothing here would make a
  stranger stop scrolling, return an empty list and say why.
- A segment is a continuous range from start_beat to end_beat (inclusive) of THIS episode.
- Total length of all segments: 20-60 seconds (sweet spot 25-45). Compute it from the timestamps.
- The first beat of the first segment must BE the hook (or start <1 s before it).
- The last beat of the last segment is the cliffhanger: stop before the answer/resolution.
- Prefer one continuous segment; use 2-3 segments only to cut slow middle material or bolt a hook onto a payoff.
- Promos from one episode should be clearly different moments, not overlapping variants.

Reply with JSON only:
{{"episode": {ep.number},
  "promos": [
    {{"title": "4-7 word internal name",
      "segments": [{{"start_beat": "E{ep.number:02d}.B000", "end_beat": "E{ep.number:02d}.B000"}}],
      "hook": "what happens / is said in the first 3 seconds",
      "cliffhanger": "the unresolved moment we cut on",
      "why": "one line on why this works as an ad",
      "needs_context": "what a stranger might not get, or 'nothing'",
      "scores": {{"hook": 0-10, "cliffhanger": 0-10, "standalone": 0-10}}
    }}
  ],
  "skip_reason": ""}}"""


def cross_prompt(show_name: str, episodes: list[Episode], highlights: dict[int, list[str]],
                 accepted: list[dict]) -> str:
    logl = "\n".join(f"E{e.number:02d} ({sec_to_ts(e.duration)}): {e.logline}" for e in episodes)
    hl = "\n\n".join(f"# E{n:02d}\n" + "\n".join(lines) for n, lines in highlights.items())
    acc = "\n".join(f"- {a['id']} E{a['episodes']} {a['segments']} \"{a['title']}\" hook: {a['hook'][:80]}"
                    for a in accepted)
    return f"""SHOW: {show_name}
TASK: build STITCHED promos that join moments from DIFFERENT episodes into one 20-60 s ad
(the trailer-style promos microdrama apps run: e.g. humiliation in one episode -> the twist it leads to).

ALL EPISODES (loglines):
{logl}

HIGHLIGHT BEATS PER EPISODE (strongest lines by keyword score, in time order; beat_id [start-end] WHO: text):
{hl}

SINGLE-EPISODE PROMOS ALREADY CHOSEN (do not just repeat these):
{acc}

INSTRUCTIONS
- Propose up to 6 stitched promos. Each joins 2-3 segments from at least 2 different episodes, in the order
  they should play. Each segment is start_beat..end_beat inside ONE episode (inclusive). Keep segments tight:
  usually 1-6 consecutive lines. Every id must appear above.
- Total 20-60 s. Segment 1 opens on the hook. The final segment ends unresolved.
- It must read as ONE clear storyline to a stranger. Do not mix unrelated subplots. Do not spoil the series
  finale; promos exist to send people to episode 1.

Reply with JSON only:
{{"promos": [{{"title": "...", "segments": [{{"start_beat": "E01.B010", "end_beat": "E01.B014"}}, {{"start_beat": "E03.B050", "end_beat": "E03.B058"}}],
  "hook": "...", "cliffhanger": "...", "why": "...", "needs_context": "...",
  "scores": {{"hook": 0-10, "cliffhanger": 0-10, "standalone": 0-10}}}}]}}"""


def rank_prompt(show_name: str, n_eps: int, rows: list[dict]) -> str:
    table = "\n".join(
        f"- {r['id']} | E{','.join(map(str, r['episodes']))} | {r['duration']:.0f}s | {r['title']} | hook: {r['hook'][:90]} | "
        f"cliff: {r['cliffhanger'][:90]} | model h/c/s {r['llm_scores'].get('hook', '-')}/{r['llm_scores'].get('cliffhanger', '-')}/"
        f"{r['llm_scores'].get('standalone', '-')} | auto score {r['score']:.2f} | checks {'PASS' if r['checks'].get('passed') else 'FAIL'}"
        for r in rows)
    return f"""SHOW: {show_name} ({n_eps} episodes). The promos below passed our automatic checks.
You are deciding the POSTING ORDER for the launch week. Viewers who click land on episode 1.

CANDIDATES:
{table}

Pick the 10 you would post first, in order. Consider: strength of the 3-second hook, how badly the viewer
needs the next scene, clarity for a stranger, distance from episode 1 (a promo from E3 pays off within minutes
of clicking; one from E45 needs 45 episodes of patience and can spoil), and variety (do not post three promos
of the same scene back to back; vary the emotional angle: humiliation, romance, revenge, mystery).

Reply with JSON only:
{{"order": [{{"id": "...", "reason": "one line: why it is in this slot"}}],
  "strategy": "2-3 sentences on the posting plan",
  "weak": [{{"id": "...", "problem": "why this one should not be posted"}}]}}"""


SYSTEM_JUDGE = """You are a fresh viewer scrolling TikTok. You have NEVER heard of this show. You only see the clip
transcript below (timestamps are seconds from the start of the clip). Judge honestly and harshly; most ads are skipped.
Reply with JSON only."""


def judge_prompt(items: list[dict]) -> str:
    body = "\n\n".join(f"### CLIP {it['id']} ({it['duration']:.0f}s)\n" + "\n".join(it["lines"]) for it in items)
    return f"""{body}

For EACH clip answer:
- stop: 0-10, would the first 3 seconds make you stop scrolling?
- clear: 0-10, do you understand who the people are and what is at stake without any other context?
- next: 0-10, how badly do you need to know what happens next?
- confusing: the single most confusing thing, or "nothing"
- would_click: true/false

Reply with JSON only: {{"clips": [{{"id": "...", "stop": 0, "clear": 0, "next": 0, "confusing": "...", "would_click": false}}]}}"""
