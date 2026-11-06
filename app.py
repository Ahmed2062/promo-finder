"""Streamlit UI:  streamlit run app.py

Upload a .zip of screenplays (or point at a local folder), pick a model, get ranked promos.
"""
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from promofinder import pipeline
from promofinder.llm import PRESETS, PendingPrompts
from promofinder.pipeline import run

DEMO = Path(__file__).parent / "samples" / "demo_show"


def secret(name):
    """A key the app owner stored in Streamlit Cloud secrets. Root-level secrets are exposed as environment
    variables, so this never touches st.secrets (which shows an error box when no secrets file exists)."""
    return os.environ.get(name) or None



st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
# providers whose key the app owner put in Streamlit secrets -> usable with no key from the visitor
BUILT_IN = {p: secret(PRESETS[p][3]) for p in PRESETS if PRESETS[p][3] and secret(PRESETS[p][3])}
st.title("🎬 Promo Finder")
st.caption("Reads a microdrama screenplay pack and finds the best 20–60 s promos: hook in 3 s, cut on a cliffhanger, "
           "makes sense to a stranger.")

with st.sidebar:
    st.header("Model")
    choices = ["none"] + [p for p in PRESETS if p not in ("manual", "none")]
    default = next((choices.index(p) for p in ("gemini", "groq", "openrouter") if p in BUILT_IN), 0)
    provider = st.selectbox("Provider", choices, index=default,
                            help="'none' runs the free heuristic pipeline only (no key needed).")
    model = st.text_input("Model (blank = default)", "",
                          help=f"Default for {provider}: {PRESETS[provider][2]}" if provider != "none" else None)
    key = None
    if provider in BUILT_IN:
        st.caption("✅ Uses the app's built-in free-tier key. Paste your own below to use your quota instead.")
        key = st.text_input("API key (optional)", type="password") or BUILT_IN[provider]
    elif provider not in ("none", "ollama"):
        key = st.text_input("API key", type="password", help="Not stored.")
    top = st.slider("Promos to rank", 5, 30, 15)
    judge = st.checkbox("Blind-viewer QA pass", True)
    if provider != "none":
        st.caption("LLM mode makes one call per episode plus 3 more. On a free tier a 50-episode show takes "
                   "roughly 5–10 minutes (rate limits); try the demo or a few episodes first.")
    eps_txt = st.text_input("Episodes (optional, e.g. 1-5)", "")

src = st.radio("Screenplays", ["Upload .zip", "Demo sample (synthetic)", "Local folder"], horizontal=True)
folder = None
if src == "Upload .zip":
    up = st.file_uploader("Zip with one .txt per episode", type="zip")
    if up:
        tmp = Path(tempfile.mkdtemp())
        zipfile.ZipFile(io.BytesIO(up.read())).extractall(tmp)
        folder = str(tmp)
elif src == "Demo sample (synthetic)":
    folder = str(DEMO)
    st.caption("Two short made-up episodes written for this demo (not Kuku content), in the same screenplay format.")
else:
    folder = st.text_input("Folder path (local runs only)", "data")
st.caption("Scripts are processed in memory for this session only and are not stored. With provider 'none' it runs "
           "free in seconds; an LLM provider adds the editor, ranking and blind-viewer stages.")


def parse_eps(t):
    out = []
    for part in t.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out or None


if st.button("Find promos", type="primary", disabled=not folder):
    out = Path(tempfile.mkdtemp())
    try:
        episodes = parse_eps(eps_txt)
    except ValueError:
        st.error("Episodes should look like 1-5 or 1,3,7")
        st.stop()
    with st.status("Reading episodes and picking promos…", expanded=True) as status:
        pipeline.PROGRESS = lambda m: status.update(label=m)
        try:
            res = run(folder, str(out), provider, model or None, None, key or None,
                      "Demo show" if src.startswith("Demo") else None, top, judge, episodes)
        except PendingPrompts as e:
            status.update(label="Stopped", state="error")
            st.error(str(e))
            st.stop()
        except (Exception, SystemExit) as e:  # bad key, model name, network, no scripts found...
            status.update(label="Run failed", state="error")
            st.error(f"Run failed: {e}")
            st.stop()
        finally:
            pipeline.PROGRESS = None
        status.update(label=f"Done in {res['seconds']}s", state="complete", expanded=False)
    st.session_state["res"], st.session_state["out"] = res, out

res = st.session_state.get("res")
if res:
    q = res["quality"]
    c = st.columns(4)
    c[0].metric("Ranked promos", len(res["ranked"]))
    c[1].metric("Passed checks", f"{q['pass_rate'] * 100:.0f}%")
    c[2].metric("Auto-repaired", q["auto_repaired"])
    c[3].metric("Avg length", f"{q['avg_duration_ranked']}s")
    if res.get("strategy"):
        st.info(res["strategy"])
    for p in res["ranked"]:
        segs = " + ".join(f"E{s['episode']} {s['start']}–{s['end']}" for s in p["segments"])
        with st.expander(f"#{p['rank']}  {p['title']}  ·  {p['duration']:.0f}s  ·  {segs}", expanded=p["rank"] <= 3):
            st.markdown(f"**Hook:** {p['hook']}  \n**Cliffhanger:** {p['cliffhanger']}  \n**Why:** {p['why']}  \n"
                        f"**Why this slot:** {p['rank_reason']}")
            if p.get("repairs"):
                st.caption("Auto-repairs: " + "; ".join(p["repairs"]))
            st.code("\n".join(p.get("transcript", [])), language=None)
    out = st.session_state["out"]
    d = st.columns(3)
    d[0].download_button("promos.json", json.dumps(res, indent=1, ensure_ascii=False), "promos.json")
    d[1].download_button("promos_ranked.csv", (out / "promos_ranked.csv").read_bytes(), "promos_ranked.csv")
    d[2].download_button("report.html", (out / "report.html").read_bytes(), "report.html")
