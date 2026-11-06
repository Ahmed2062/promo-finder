"""CLI:  python -m promofinder --input data/ --out outputs/ --provider gemini"""
import argparse
import sys

from .llm import PRESETS, PendingPrompts
from .pipeline import run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="promofinder", description="Find the best 20-60s promos in a microdrama screenplay pack.")
    ap.add_argument("--input", "-i", required=True, help="folder with one .txt screenplay per episode")
    ap.add_argument("--out", "-o", default="outputs", help="output folder")
    ap.add_argument("--provider", "-p", default="none", choices=list(PRESETS),
                    help="LLM provider (none = heuristics only, manual = paste prompts into any chat UI)")
    ap.add_argument("--model", "-m", help="model name override, e.g. claude-sonnet-5-5, gemini-2.0-flash")
    ap.add_argument("--base-url", help="override API base url (any OpenAI-compatible server)")
    ap.add_argument("--show-name", help="display name of the show")
    ap.add_argument("--top", type=int, default=15, help="how many promos to rank")
    ap.add_argument("--episodes", help="subset, e.g. 1-10 or 3,5,8")
    ap.add_argument("--no-judge", action="store_true", help="skip the blind-viewer QA call")
    ap.add_argument("--redact", action="store_true", help="omit script transcripts from outputs (for public sharing)")
    a = ap.parse_args(argv)
    eps = None
    if a.episodes:
        eps = []
        for part in a.episodes.split(","):
            if "-" in part:
                lo, hi = part.split("-")
                eps += list(range(int(lo), int(hi) + 1))
            else:
                eps.append(int(part))
    try:
        run(a.input, a.out, a.provider, a.model, a.base_url, None, a.show_name, a.top, not a.no_judge, eps, a.redact)
    except PendingPrompts:
        sys.exit(2)


if __name__ == "__main__":
    main()
