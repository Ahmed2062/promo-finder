"""Parse timestamped microdrama screenplays into structured beats.

The input format (one .txt per episode) looks like:

    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    SCENE 01
    Scene heading: INT. LOCATION – DAY
    Scene start: 00:00.00
    **CAMERA:**
    Start: (00:00.00) | End: (00:04.80)
    Wide shot of ...
    **CHARACTER NAME (Emotion, Emotion):**
    Start: (00:13.50) | End: (00:14.30)
    "Line of dialogue."

The parser is deliberately forgiving (case-insensitive keys, optional fields)
so a new show with small format drift still parses. Every beat gets a stable
id like ``E01.B017`` that the LLM refers to instead of raw timestamps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

TS = r"(\d{1,2}):(\d{2}(?:\.\d+)?)"
RE_BEAT_HEADER = re.compile(r"^\*\*(.+?):?\*\*:?\s*$")
RE_TIMING = re.compile(rf"Start:\s*\(?\s*{TS}\s*\)?\s*\|\s*End:\s*\(?\s*{TS}\s*\)?", re.I)
RE_SCENE = re.compile(r"^SCENE\s+(\d+)\s*$", re.I)
RE_KV = re.compile(r"^(EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES|SCENE HEADING|SCENE START|SCENE END)\s*:\s*(.*)$", re.I)
RE_EP_FILE = re.compile(r"episode[-_ ]?(\d+)", re.I)

NON_SPEAKER = {"CAMERA": "camera", "ACTION": "action", "SOUND FX": "sfx", "SFX": "sfx",
               "SOUND": "sfx", "MUSIC": "sfx", "TEXT ON SCREEN": "text", "SUPER": "text"}
VOICE_TAGS = {"O.S.", "V.O.", "CONT'D", "OS", "VO", "O.C."}


def ts_to_sec(m: str, s: str) -> float:
    return int(m) * 60 + float(s)


def sec_to_ts(t: float) -> str:
    t = max(0.0, t)
    m = int(t // 60)
    return f"{m:02d}:{t - m * 60:05.2f}"


@dataclass
class Beat:
    id: str
    episode: int
    idx: int
    scene: int
    kind: str          # dialogue | camera | action | sfx | text
    speaker: str       # "" for non-dialogue
    tags: list[str]    # parenthetical tags, e.g. ["Gloating", "Smug"], ["O.S."]
    start: float
    end: float
    text: str

    @property
    def emotion(self) -> str:
        return ", ".join(t for t in self.tags if t.upper() not in VOICE_TAGS)

    def line(self, rel_to: float | None = None) -> str:
        t0 = self.start if rel_to is None else self.start - rel_to
        t1 = self.end if rel_to is None else self.end - rel_to
        who = {"dialogue": f"{self.speaker}" + (f" ({self.emotion})" if self.emotion else ""),
               "camera": "CAM", "action": "ACTION", "sfx": "SFX", "text": "TEXT"}[self.kind]
        return f"{self.id} [{sec_to_ts(t0)}-{sec_to_ts(t1)}] {who}: {self.text}"


@dataclass
class Scene:
    number: int
    heading: str
    start: float | None
    end: float | None


@dataclass
class Episode:
    number: int
    title: str
    logline: str
    continuity: str
    source: str
    scenes: list[Scene] = field(default_factory=list)
    beats: list[Beat] = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [b.end for b in self.beats] + [s.end for s in self.scenes if s.end is not None]
        return max(ends) if ends else 0.0

    def beat(self, beat_id: str) -> Beat | None:
        return self._index().get(beat_id)

    def _index(self) -> dict[str, Beat]:
        if not hasattr(self, "_idx_cache"):
            object.__setattr__(self, "_idx_cache", {b.id: b for b in self.beats})
        return self._idx_cache  # type: ignore[attr-defined]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = self.duration
        return d


def _split_header(h: str) -> tuple[str, list[str]]:
    tags = re.findall(r"\(([^)]*)\)", h)
    name = re.sub(r"\([^)]*\)", "", h).strip().rstrip(":").strip()
    flat: list[str] = []
    for t in tags:
        flat.extend(x.strip() for x in t.split(",") if x.strip())
    return name, flat


def parse_episode(path: str | Path) -> Episode:
    path = Path(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    m = RE_EP_FILE.search(path.name)
    number = int(m.group(1)) if m else 0
    ep = Episode(number=number, title="", logline="", continuity="", source=path.name)

    scene_no = 0
    cur: dict | None = None   # beat under construction

    def flush():
        nonlocal cur
        if cur and cur.get("start") is not None:
            text = " ".join(cur["text"]).strip().strip('"').strip("“”").strip()
            idx = len(ep.beats)
            ep.beats.append(Beat(
                id=f"E{ep.number:02d}.B{idx:03d}", episode=ep.number, idx=idx, scene=scene_no,
                kind=cur["kind"], speaker=cur["speaker"], tags=cur["tags"],
                start=cur["start"], end=max(cur["end"], cur["start"]), text=text))
        cur = None

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("---") or line.lower().startswith("actions and dialogue"):
            continue
        kv = RE_KV.match(line)
        if kv:
            key, val = kv.group(1).upper(), kv.group(2).strip()
            if key == "EPISODE NUMBER":
                ep.title = val
                n = re.search(r"\d+", val)
                if n and not number:
                    ep.number = int(n.group())
            elif key == "EPISODE LOGLINE":
                ep.logline = val
            elif key == "CONTINUITY NOTES":
                ep.continuity = val
            elif key == "SCENE HEADING" and ep.scenes:
                ep.scenes[-1].heading = val
            elif key in ("SCENE START", "SCENE END") and ep.scenes:
                t = re.search(TS, val)
                if t:
                    setattr(ep.scenes[-1], "start" if key == "SCENE START" else "end", ts_to_sec(*t.groups()))
            continue
        sm = RE_SCENE.match(line)
        if sm:
            flush()
            scene_no = int(sm.group(1))
            ep.scenes.append(Scene(scene_no, "", None, None))
            continue
        hm = RE_BEAT_HEADER.match(line)
        if hm:
            flush()
            name, tags = _split_header(hm.group(1))
            kind = NON_SPEAKER.get(name.upper(), "dialogue")
            cur = {"kind": kind, "speaker": "" if kind != "dialogue" else name.upper(),
                   "tags": tags, "start": None, "end": None, "text": []}
            continue
        tm = RE_TIMING.search(line)
        if tm and cur is not None and cur["start"] is None:
            cur["start"] = ts_to_sec(tm.group(1), tm.group(2))
            cur["end"] = ts_to_sec(tm.group(3), tm.group(4))
            continue
        if cur is not None:
            cur["text"].append(line)
    flush()
    ep.beats.sort(key=lambda b: (b.start, b.idx))
    for i, b in enumerate(ep.beats):          # ids follow screen time order
        b.idx, b.id = i, f"E{ep.number:02d}.B{i:03d}"
    return ep


def long_path(p: str | Path) -> Path:
    """On Windows, prefix with \\\\?\\ so paths over 260 chars still work."""
    import os
    p = os.path.abspath(str(p))
    if os.name == "nt" and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    return Path(p)


def load_show(folder: str | Path) -> list[Episode]:
    folder = long_path(folder)
    files = sorted(p for p in folder.rglob("*.txt"))
    eps = [parse_episode(p) for p in files]
    eps = [e for e in eps if e.beats]
    eps.sort(key=lambda e: e.number)
    return eps
