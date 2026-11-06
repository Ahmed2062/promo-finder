"""Streamlit UI:  streamlit run app.py

Upload a .zip of screenplays (or point at a local folder), pick a model, get ranked promos.
"""
import io
import json
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from promofinder.llm import PRESETS, PendingPrompts
from promofinder.pipeline import run

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
st.title("🎬 Promo Finder")
st.caption("Reads a microdrama screenplay pack and finds the best 20–60 s promos: hook in 3 s, cut on a cliffhanger, "
           "makes sense to a stranger.")

with st.sidebar:
    st.header("Model")
    choices = ["none"] + [p for p in PRESETS if p not in ("manual", "none")]
    provider = st.selectbox("Provider", choices, index=0,
                            help="'none' runs the free heuristic pipeline only (no key needed).")
    model = st.text_input("Model (blank = default)", "")
    key = st.text_input("API key", type="password", help="Not stored. Ollama needs none.") if provider not in ("none", "ollama") else None
    top = st.slider("Promos to rank", 5, 30, 15)
    judge = st.checkbox("Blind-viewer QA pass", True)

src = st.radio("Screenplays", ["Upload .zip", "Local folder"], horizontal=True)
folder = None
if src == "Upload .zip":
    up = st.file_uploader("Zip with one .txt per episode", type="zip")
    if up:
        tmp = Path(tempfile.mkdtemp())
        zipfile.ZipFile(io.BytesIO(up.read())).extractall(tmp)
        folder = str(tmp)
else:
    folder = st.text_input("Folder path (local runs only)", "data")
st.caption("Scripts are processed in memory for this session only and are not stored. With provider 'none' it runs "
           "free in seconds; an API key enables the LLM stages.")

if st.button("Find promos", type="primary", disabled=not folder):
    out = Path(tempfile.mkdtemp())
    with st.spinner("Reading episodes and picking promos…"):
        try:
            res = run(folder, str(out), provider, model or None, None, key or None, None, top, judge)
        except PendingPrompts as e:
            st.error(str(e))
            st.stop()
        except Exception as e:  # bad key, model name, network...
            st.error(f"Run failed: {e}")
            st.stop()
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
