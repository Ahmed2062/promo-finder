# Promo Finder

Reads a folder of timestamped microdrama screenplays and returns the best 20–60 s promos. For each promo it gives:
- the episode, start and end of every segment (in playback order)
- the hook
- the cliffhanger
- one line on why it works
- a ranked posting order

It also checks its own output and reports where it fails.

Built for Kuku's AI Generalist assignment, Problem 1 ("Find the Promos").

```
screenplays/*.txt ──► parse ──► heuristic shortlist ──► LLM picks (by beat id) ──► repair + rule checks ──► rank ──► blind-viewer QA ──► JSON / CSV / MD / HTML
                      code       code (free, ~5 s)       1 call / episode           code                     code + 1 call   1 call
```

## Quick start

```bash
pip install -r requirements.txt            # streamlit + requests + anthropic; the CLI itself needs only requests
python -m promofinder -i data/ -o outputs/heuristic --provider none        # free, offline, ~5 s
```

Put the scripts in `data/` (one `.txt` per episode; sub-folders are fine). `data/` is git-ignored because the input pack is confidential.

### With an LLM (swap models with one flag)

| `--provider` | what it uses | key env var | cost |
|---|---|---|---|
| `none` | heuristics only | – | free |
| `gemini` | Google AI Studio, OpenAI-compatible endpoint | `GEMINI_API_KEY` | free tier |
| `groq` | Groq (Llama 3.3 70B by default) | `GROQ_API_KEY` | free tier |
| `openrouter` | any OpenRouter model (`:free` ones too) | `OPENROUTER_API_KEY` | free tier |
| `ollama` | local model at `localhost:11434` | – | free |
| `anthropic` | Claude via the official SDK (`claude-opus-5-5` default) | `ANTHROPIC_API_KEY` | paid |
| `openai` | OpenAI, or any compatible server via `--base-url` | `OPENAI_API_KEY` | paid |
| `manual` | writes each prompt to `outputs/<run>/llm/pending/`; paste it into any chat UI and save the reply as `<name>.response.txt` | – | free |

```bash
python -m promofinder -i data/ -o outputs/run1 --provider gemini --model gemini-2.0-flash
python -m promofinder -i data/ -o outputs/run2 --provider anthropic --model claude-sonnet-5-5
python -m promofinder -i data/ -o outputs/try --provider groq --episodes 1-5     # quick subset
```

Model names change often, so `--model` overrides the default for every provider.

Every prompt and raw reply is saved in `outputs/<run>/llm/`. Replies are cached by prompt hash, so a re-run costs nothing and gives the same result.

### UI

```bash
streamlit run app.py
```

Upload a zip of scripts, pick a provider, and download the JSON, CSV or HTML report. It deploys as-is to Streamlit Community Cloud. Keep the repo private, or don't bundle the scripts.

## Outputs (per run)

| file | what |
|---|---|
| `report.html` | the editor view: ranked cards with a timeline of each cut, hook, cliffhanger, checks, blind-viewer scores and the transcript of the cut |
| `promos.json` | everything (ranked, other valid, failed checks, rejected LLM output, quality stats, transcripts) |
| `promos_ranked.csv` | one row per promo, ready for a spreadsheet or an editing queue |
| `promos.md` | readable summary |
| `01_parsed_episodes.json`, `02_heuristic_candidates.json` | intermediate stages |
| `llm/*.prompt.md`, `llm/*.response.txt` | raw, unedited prompts and model replies |

## How it decides

- **The LLM points, code measures.** The model gets each episode as numbered beats (`E12.B041 [01:03.10-01:05.40] CLARISSA (Cold): …`) and answers with beat ids, never timestamps. Code converts ids to times, so timecodes can't be hallucinated.
- **Safe cuts.** Every start and end is snapped to a point where no line of dialogue is in progress.
- **Hard rules in code.**
  - 20–60 s long.
  - A hook beat starts within the first 3 s.
  - An unresolved beat in the last ~5 s. An episode ending counts unless the last beats are resolution beats (smiles, embraces, "resolving chord").
  - At least 3 lines of dialogue.
  - Every segment at least 2.5 s.
- **Automatic repair.** If a pick runs over 60 s, it becomes *hook (≈8 s) + payoff*, two segments, which is what an editor would do. If it's under 20 s, it starts earlier, keeping the cliffhanger ending.
- **Ranking:**
  - Score = 0.32·hook + 0.30·cliffhanger + 0.20·standalone + 0.08·intensity + 0.10·funnel − 0.12 per failed check.
  - Hook, cliffhanger and standalone each blend 70% model score with 30% heuristic score.
  - "Funnel" favours early episodes, because a clicked ad lands the viewer on episode 1.
  - A diversity pass avoids posting the same scene twice in a row.
  - The LLM then sets the final posting order from the top 25 and gives a reason for each slot.
- **Blind-viewer QA.** A separate call sees only the clip's lines, with no show context. It rates stop-scrolling, clarity and need-to-know-next. This is a stand-alone test for "makes sense on its own", and it doesn't affect the ranking.

## Code map

```
promofinder/parser.py      screenplay -> Episode/Beat (forgiving parser, stable beat ids)
promofinder/signals.py     keyword/structure scores per beat: hook, cliff, resolution
promofinder/core.py        Promo/Segment, safe cut points, transcripts, validation, repair
promofinder/candidates.py  heuristic shortlist + stitched cold-opens
promofinder/prompts.py     all prompts in one place
promofinder/llm.py         providers, disk cache, manual mode
promofinder/pipeline.py    the 8 stages
promofinder/report.py      Markdown + self-contained HTML
app.py                     Streamlit UI
```
