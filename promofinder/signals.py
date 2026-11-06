"""Cheap, explainable per-beat signals (no LLM).

Two scores per beat, both in [0, 1]:
  * hook  - how likely this beat stops a scrolling thumb in <3 s
            (violence, public humiliation, a reveal, a shocking line, a big emotion)
  * cliff - how unresolved the story is if we cut right after this beat
            (a question, a threat, an ultimatum, an interruption, an arrival)

These are keyword/structure heuristics written for vertical melodrama. They
are a *pre-filter* and a sanity check for the LLM, not the final judge.
"""
from __future__ import annotations

import math
import re

from .parser import Beat

# weight, patterns (matched on lower-cased text)
HOOK_LEXICON: list[tuple[float, list[str]]] = [
    (3.0, ["slap", "slaps", "strikes her", "strikes him", "punch", "stab", "explosion", "explodes",
           "blood", "bleeding", "slit", "throat", "red-hot", "branding", "riding crop", "whip",
           "collapses", "unconscious", "dead body", "noose", "gallows", "hang", "execution", "burn her",
           "set ablaze", "inferno", "fire", "poison", "arsenic", "witch"]),
    (2.5, ["kneel", "on one knee", "proposal", "marry me", "abandon", "jilted", "betray", "traitor",
           "liar", "how dare you", "you will pay", "disowned", "disinherit", "pregnant", "affair",
           "arrest", "seize her", "guards", "chains", "chained", "dungeon", "framed", "frame her",
           "duke", "duchess", "true identity", "who are you", "alive", "presumed dead", "return from the dead",
           "your grace", "imposter", "pounding", "bursts in", "antidote"]),
    (1.8, ["kiss", "embrace", "tears", "sob", "scream", "shout", "gasp", "stunned", "shock", "shocked",
           "humiliat", "mock", "laugh at", "spit", "shove", "grabs", "drags", "throws", "mud",
           "ring", "signet", "crest", "secret", "truth", "never", "destroy", "ruin", "kill", "die"]),
    (1.0, ["cold", "cruel", "smirk", "sneer", "glare", "furious", "rage", "trembl", "desperate",
           "beg", "plead", "please", "father", "mother", "sister", "husband", "wife", "wedding", "altar"]),
]

EMOTION_LEXICON: list[tuple[float, list[str]]] = [
    (2.0, ["furious", "screaming", "enraged", "murderous", "horrified", "devastated", "shocked",
           "outraged", "venomous", "sadistic", "menacing", "lethal", "explosive", "roaring", "gasping"]),
    (1.2, ["cold", "cruel", "mocking", "gloating", "smug", "contempt", "disgusted", "defiant", "desperate",
           "pleading", "begging", "trembling", "broken", "betrayed", "accusing", "threatening", "commanding",
           "icy", "seething", "fierce", "vicious", "scornful", "triumphant"]),
]

CLIFF_LEXICON: list[tuple[float, list[str]]] = [
    (3.0, ["cliffhanger", "cut to black", "smash cut", "freeze", "bursts in", "burst open", "doors fly open",
           "who are you", "or watch", "or he", "or she", "before it's too late", "you have until",
           "is invalid", "unaware", "witnessed", "someone is watching", "footsteps approach",
           "or leave", "or die", "take her", "take him", "seize", "read the first", "your grace"]),
    (2.2, ["will pay", "will regret", "will hang", "will burn", "will die", "tomorrow", "at dawn",
           "unless", "or else", "if you", "i know what you", "the truth is", "you're not", "is not who",
           "arrives", "appears", "enters", "doorway", "door creaks", "door opens", "silhouette",
           "ominous", "drone", "swell", "stunned silence", "gasp"]),
    (1.2, ["what", "why", "how", "who", "never", "wait", "no...", "stop", "secret", "lie", "truth", "mine"]),
]

# words that signal the tension has been released (bad place to cut a promo)
RESOLUTION_LEXICON: list[tuple[float, list[str]]] = [
    (2.5, ["resolving chord", "resolves", "relief", "at peace", "peaceful", "contented", "content smile",
           "walk away together", "walk out into the light", "hand in hand", "gentle smile", "warm smile",
           "soft smile", "tender", "safe now", "it's over", "thank you", "triumphant musical", "fading out"]),
    (1.2, ["smile", "smiles", "embrace", "hug", "laughs softly", "calm", "warmth", "fade out"]),
]

CONTEXT_STARTERS ={"and", "but", "so", "then", "because", "he", "she", "it", "they", "that", "this",
                    "those", "yes", "no", "also", "still", "again", "instead"}


def _lex(text: str, lexicon: list[tuple[float, list[str]]]) -> tuple[float, list[str]]:
    t = text.lower()
    score, hits = 0.0, []
    for w, words in lexicon:
        for word in words:
            if re.search(r"(?<![a-z])" + re.escape(word), t):
                score += w
                hits.append(word)
    return score, hits


def squash(x: float, k: float = 4.0) -> float:
    return 1.0 - math.exp(-max(x, 0.0) / k)


def beat_signals(b: Beat) -> dict:
    """Return hook/cliff scores plus the words that triggered them (for explanations)."""
    text = b.text
    h, hh = _lex(text, HOOK_LEXICON)
    c, ch = _lex(text, CLIFF_LEXICON)
    e, eh = _lex(b.emotion, EMOTION_LEXICON)
    r, rh = _lex(text, RESOLUTION_LEXICON)
    c -= r
    if rh:
        ch = ch + [f"-{w}" for w in rh]

    if b.kind == "dialogue":
        h += 0.6 * e
        excl = text.count("!")
        h += min(excl, 2) * 0.8
        if text.rstrip().endswith("?"):
            c += 1.6
        if text.rstrip().endswith(("...", "—", "-")):
            c += 1.0                       # trailing off / interrupted
        if len(text) < 40 and excl:
            h += 0.6                       # short punchy line
        weight = 1.0
    elif b.kind == "action":
        weight = 0.9
    elif b.kind == "sfx":
        weight = 0.8
        c *= 1.3
    else:                                   # camera notes describe visuals; useful but noisy
        weight = 0.55
    return {
        "resolved": bool(rh),
        "hook": round(squash(h * weight), 3),
        "cliff": round(squash(c * weight, 3.5), 3),
        "hook_hits": hh + eh,
        "cliff_hits": ch,
    }


def starts_mid_context(text: str) -> bool:
    first = re.sub(r"^[^a-zA-Z]+", "", text).split(" ", 1)[0].lower().strip(",.!?")
    return first in CONTEXT_STARTERS
