"""Promo / segment model, safe cut points, transcripts and rule checks.

Design rule: the LLM never invents timestamps. It points at beat ids; this
module turns beats into timestamps, snaps every cut to a point where nobody is
mid-sentence, and checks the hard constraints (20-60 s, hook in the first 3 s,
unresolved ending) in plain code.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from functools import lru_cache

from .parser import Episode, Beat, sec_to_ts
from .signals import beat_signals, starts_mid_context

MIN_LEN, MAX_LEN = 20.0, 60.0
HOOK_WINDOW = 3.0
TOL = 0.15


@dataclass
class Segment:
    episode: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def to_dict(self) -> dict:
        return {"episode": self.episode, "start": sec_to_ts(self.start), "end": sec_to_ts(self.end),
                "start_sec": round(self.start, 2), "end_sec": round(self.end, 2), "duration": self.duration}


@dataclass
class Promo:
    id: str
    segments: list[Segment]
    title: str = ""
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    source: str = "heuristic"           # heuristic | llm | llm-cross
    llm_scores: dict = field(default_factory=dict)
    heur: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    repairs: list[str] = field(default_factory=list)
    score: float = 0.0
    rank: int | None = None
    rank_reason: str = ""
    judge: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def duration(self) -> float:
        return round(sum(s.duration for s in self.segments), 2)

    @property
    def episodes(self) -> list[int]:
        return sorted({s.episode for s in self.segments})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["segments"] = [s.to_dict() for s in self.segments]
        d["duration"] = self.duration
        d["episodes"] = self.episodes
        return d


class Show:
    """All episodes plus cached per-beat signals and cut points."""

    def __init__(self, episodes: list[Episode], name: str = "show"):
        self.name = name
        self.episodes = {e.number: e for e in episodes}
        self.sig: dict[str, dict] = {}
        for e in episodes:
            for b in e.beats:
                self.sig[b.id] = beat_signals(b)
        self._cuts = {e.number: self._safe_cuts(e) for e in episodes}

    # ---- cut points -------------------------------------------------------
    @staticmethod
    def _safe_cuts(ep: Episode) -> list[float]:
        pts = {0.0, round(ep.duration, 2)}
        for s in ep.scenes:
            for t in (s.start, s.end):
                if t is not None:
                    pts.add(round(t, 2))
        for b in ep.beats:
            pts.add(round(b.start, 2))
            pts.add(round(b.end, 2))
        dialog = [(b.start, b.end) for b in ep.beats if b.kind == "dialogue"]
        safe = [t for t in sorted(pts) if not any(s + TOL < t < e - TOL for s, e in dialog)]
        return safe

    def cuts(self, ep: int) -> list[float]:
        return self._cuts[ep]

    def snap_start(self, ep: int, t: float) -> float:
        c = [x for x in self._cuts[ep] if x <= t + 0.3]
        return c[-1] if c else self._cuts[ep][0]

    def snap_end(self, ep: int, t: float) -> float:
        c = [x for x in self._cuts[ep] if x >= t - 0.3]
        return c[0] if c else self._cuts[ep][-1]

    # ---- content ------------------------------------------------------------
    def beats_in(self, seg: Segment, kinds=("dialogue", "action", "sfx", "camera")) -> list[Beat]:
        ep = self.episodes[seg.episode]
        return [b for b in ep.beats if b.kind in kinds and b.start >= seg.start - TOL and b.start < seg.end - TOL]

    def transcript(self, promo: Promo, kinds=("dialogue", "action", "sfx"), relative=True) -> list[str]:
        out, offset = [], 0.0
        for i, s in enumerate(promo.segments):
            if len(promo.segments) > 1:
                out.append(f"--- segment {i + 1}: episode {s.episode} {sec_to_ts(s.start)}-{sec_to_ts(s.end)} ---")
            for b in self.beats_in(s, kinds):
                rel = s.start - offset if relative else None
                out.append(b.line(rel_to=rel))
            offset += s.duration
        return out

    def first_line(self, promo: Promo, kinds=("dialogue",)) -> Beat | None:
        for s in promo.segments:
            bs = self.beats_in(s, kinds)
            if bs:
                return bs[0]
        return None

    def last_line(self, promo: Promo, kinds=("dialogue",)) -> Beat | None:
        for s in reversed(promo.segments):
            bs = self.beats_in(s, kinds)
            if bs:
                return bs[-1]
        return None

    # ---- signals over a promo ----------------------------------------------
    def hook_strength(self, promo: Promo) -> tuple[float, Beat | None]:
        """Strongest hook beat that starts within the first 3 s of the promo."""
        s = promo.segments[0]
        best, best_b = 0.0, None
        for b in self.beats_in(s):
            if b.start - s.start > HOOK_WINDOW:
                break
            v = self.sig[b.id]["hook"]
            if v > best:
                best, best_b = v, b
        return best, best_b

    def cliff_strength(self, promo: Promo) -> tuple[float, Beat | None]:
        """Strongest unresolved beat in the last ~5 s; episode endings get a floor
        because these scripts are written to end every episode on a cliffhanger."""
        s = promo.segments[-1]
        ep = self.episodes[s.episode]
        best, best_b, resolved = 0.0, None, False
        for b in self.beats_in(s):
            if s.end - b.end <= 5.0 or (b.kind == "dialogue" and s.end - b.start <= 6.0):
                v = self.sig[b.id]["cliff"]
                resolved |= self.sig[b.id]["resolved"]
                if b.kind == "dialogue":
                    v = min(1.0, v * 1.15)
                if v >= best:
                    best, best_b = v, b
        if abs(s.end - ep.duration) < 1.0 and not resolved:
            best = max(best, 0.75)
        return best, best_b

    def intensity(self, promo: Promo) -> float:
        vals = sorted((self.sig[b.id]["hook"] for s in promo.segments for b in self.beats_in(s)), reverse=True)
        return round(sum(vals[:6]) / 6, 3) if vals else 0.0

    def standalone_proxy(self, promo: Promo) -> float:
        fl = self.first_line(promo)
        score = 1.0
        if fl is None:
            return 0.2
        if starts_mid_context(fl.text):
            score -= 0.35
        n_lines = sum(len(self.beats_in(s, ("dialogue",))) for s in promo.segments)
        if n_lines < 4:
            score -= 0.3
        if len(promo.episodes) > 1 and max(promo.episodes) - min(promo.episodes) > 3:
            score -= 0.15
        return max(0.0, round(score, 2))

    # ---- validation -----------------------------------------------------------
    def validate(self, promo: Promo) -> dict:
        hook, hb = self.hook_strength(promo)
        cliff, cb = self.cliff_strength(promo)
        d = promo.duration
        dialog_cut = False
        for s in promo.segments:
            ep = self.episodes[s.episode]
            for b in ep.beats:
                if b.kind == "dialogue" and (b.start + TOL < s.start < b.end - TOL or b.start + TOL < s.end < b.end - TOL):
                    dialog_cut = True
        n_lines = sum(len(self.beats_in(s, ("dialogue",))) for s in promo.segments)
        checks = {
            "length_ok": MIN_LEN - 0.01 <= d <= MAX_LEN + 0.01,
            "hook_in_3s": hook >= 0.30,
            "ends_unresolved": cliff >= 0.30,
            "clean_cuts": not dialog_cut,
            "has_dialogue": n_lines >= 3,
            "segments_ok": all(s.duration >= 2.5 for s in promo.segments),
            "hook_strength": round(hook, 3),
            "cliff_strength": round(cliff, 3),
            "hook_beat": hb.id if hb else None,
            "cliff_beat": cb.id if cb else None,
            "dialogue_lines": n_lines,
        }
        # Hook / cliff keyword checks are hard gates for heuristic promos. For LLM promos
        # they become review warnings when the model itself rated that part >= 6: in a
        # manual audit the keyword checker was wrong far more often than the model
        # ("Take her.", "Your Grace." carry no keywords but are strong beats).
        ls = promo.llm_scores or {}
        soft = []
        for key, score_key in (("hook_in_3s", "hook"), ("ends_unresolved", "cliffhanger")):
            if not checks[key] and promo.source != "heuristic" and (ls.get(score_key) or 0) >= 6:
                soft.append(key)
        checks["warnings"] = soft
        hard = [k for k in ("length_ok", "hook_in_3s", "ends_unresolved", "clean_cuts", "has_dialogue", "segments_ok")
                if k not in soft]
        checks["passed"] = all(checks[k] for k in hard)
        promo.checks = checks
        promo.heur = {"hook": round(hook, 3), "cliff": round(cliff, 3), "intensity": self.intensity(promo),
                      "standalone": self.standalone_proxy(promo)}
        return checks

    # ---- repair -------------------------------------------------------------
    def repair(self, promo: Promo) -> Promo:
        """Snap cuts and fix length. Long single segments become hook + payoff (two
        segments), which is what a human editor does with a 90-second scene."""
        segs = []
        for s in promo.segments:
            ns = Segment(s.episode, self.snap_start(s.episode, s.start), self.snap_end(s.episode, s.end))
            if (ns.start, ns.end) != (s.start, s.end):
                promo.repairs.append(f"snapped E{s.episode} {sec_to_ts(s.start)}-{sec_to_ts(s.end)} -> "
                                     f"{sec_to_ts(ns.start)}-{sec_to_ts(ns.end)} (no mid-line cuts)")
            if ns.end > ns.start:
                segs.append(ns)
        promo.segments = segs
        if not segs:
            return promo
        d = promo.duration
        if d > MAX_LEN:
            if len(segs) == 1:
                s = segs[0]
                head_end = self.snap_end(s.episode, s.start + 8.0)
                tail_len = MAX_LEN - (head_end - s.start) - 1.0
                tail_start = self.snap_start(s.episode, s.end - tail_len)
                if tail_start < head_end + 3:
                    tail_start = self.snap_end(s.episode, s.end - tail_len)
                if s.end - tail_start + head_end - s.start <= MAX_LEN and tail_start > head_end:
                    promo.segments = [Segment(s.episode, s.start, head_end), Segment(s.episode, tail_start, s.end)]
                    promo.repairs.append(f"{d:.1f}s too long -> kept hook ({head_end - s.start:.1f}s) + "
                                         f"payoff ({s.end - tail_start:.1f}s) as two segments")
                else:
                    ns = self.snap_start(s.episode, s.end - MAX_LEN + 0.5)
                    if s.end - ns > MAX_LEN:
                        ns = self.snap_end(s.episode, s.end - MAX_LEN + 0.5)
                    promo.segments = [Segment(s.episode, ns, s.end)]
                    promo.repairs.append(f"{d:.1f}s too long -> trimmed start to {sec_to_ts(ns)}")
            else:
                # trim the start of the longest segment
                i = max(range(len(segs)), key=lambda k: segs[k].duration)
                s = segs[i]
                over = d - MAX_LEN + 0.5
                ns = self.snap_end(s.episode, s.start + over)
                if s.end - ns >= 3:
                    segs[i] = Segment(s.episode, ns, s.end)
                    promo.repairs.append(f"{d:.1f}s too long -> trimmed segment {i + 1} start to {sec_to_ts(ns)}")
        elif d < MIN_LEN:
            s = segs[-1]
            ep = self.episodes[s.episode]
            need = MIN_LEN - d + 0.3
            # prefer adding run-up before the first segment (keeps the cliffhanger ending)
            f = segs[0]
            ns = self.snap_start(f.episode, max(0.0, f.start - need))
            if promo.duration + (f.start - ns) >= MIN_LEN:
                segs[0] = Segment(f.episode, ns, f.end)
                promo.repairs.append(f"{d:.1f}s too short -> started earlier at {sec_to_ts(ns)}")
            else:
                ne = self.snap_end(s.episode, min(ep.duration, s.end + need))
                segs[-1] = Segment(s.episode, s.start, ne)
                promo.repairs.append(f"{d:.1f}s too short -> extended end to {sec_to_ts(ne)}")
        return promo


def overlap(a: Promo, b: Promo) -> float:
    """Seconds of screen time two promos share."""
    tot = 0.0
    for x in a.segments:
        for y in b.segments:
            if x.episode == y.episode:
                tot += max(0.0, min(x.end, y.end) - max(x.start, y.start))
    return tot


def iou(a: Promo, b: Promo) -> float:
    inter = overlap(a, b)
    union = a.duration + b.duration - inter
    return inter / union if union > 0 else 0.0
