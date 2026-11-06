"""Swappable LLM providers. Every call is cached on disk by prompt hash, so a
re-run is free, deterministic and auditable (the raw prompt and raw response
are kept side by side in <out>/llm/).

Providers
---------
none       no LLM, heuristic pipeline only
anthropic  Claude via the official `anthropic` SDK (ANTHROPIC_API_KEY)
openai     any OpenAI-compatible /chat/completions endpoint via `requests`:
           OpenAI, Gemini (free tier), Groq (free tier), OpenRouter, Ollama/LM Studio (local)
manual     file exchange: writes prompts to <out>/llm/pending/, you paste each into any
           chat UI (free) and save the reply as <name>.response.txt, then re-run
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

PRESETS = {
    # name: (provider, base_url, default model, api-key env var)
    "anthropic": ("anthropic", None, "claude-opus-5-5", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash", "GEMINI_API_KEY"),
    "groq": ("openai", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
    "ollama": ("openai", "http://localhost:11434/v1", "qwen2.5:14b", None),
    "manual": ("manual", None, "manual", None),
    "none": ("none", None, "none", None),
}


class PendingPrompts(Exception):
    """Raised in manual mode when some prompts still need an answer."""


class LLM:
    def __init__(self, preset: str = "none", model: str | None = None, out_dir: str | Path = "outputs",
                 base_url: str | None = None, api_key: str | None = None, temperature: float = 0.2):
        if preset not in PRESETS:
            raise ValueError(f"unknown provider {preset!r}; choose from {', '.join(PRESETS)}")
        self.preset = preset
        self.provider, default_url, default_model, key_env = PRESETS[preset]
        self.model = model or default_model
        self.base_url = base_url or default_url
        self.api_key = api_key or (os.environ.get(key_env) if key_env else None) or "none"
        self.temperature = temperature
        self.dir = Path(out_dir) / "llm"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pending: list[str] = []
        self.usage = {"calls": 0, "cached": 0, "input_chars": 0, "output_chars": 0}
        self.log = print          # the pipeline swaps in its own logger (shown live in the app)

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    # ------------------------------------------------------------------------
    def ask(self, name: str, system: str, user: str, max_tokens: int = 8000) -> str | None:
        """Return the model's text, from cache if available. In manual mode
        returns None (and records the prompt) when no answer has been saved yet."""
        h = hashlib.sha256((system + "\n\n" + user).encode()).hexdigest()[:12]
        stem = f"{name}"
        prompt_f = self.dir / f"{stem}.prompt.md"
        resp_f = self.dir / f"{stem}.response.txt"
        meta_f = self.dir / f"{stem}.meta.json"
        prompt_text = f"<!-- system -->\n{system}\n\n<!-- user -->\n{user}\n"
        self.usage["input_chars"] += len(prompt_text)

        if resp_f.exists():
            meta = json.loads(meta_f.read_text()) if meta_f.exists() else {}
            if meta.get("hash") in (h, None) or self.provider == "manual":
                # keep the prompt that was actually answered next to its answer (audit trail);
                # in manual mode a changed prompt is reused on purpose and logged
                if prompt_f.exists() and prompt_f.read_text(encoding="utf-8") != prompt_text:
                    (self.dir / f"{stem}.prompt.current.md").write_text(prompt_text, encoding="utf-8")
                elif not prompt_f.exists():
                    prompt_f.write_text(prompt_text, encoding="utf-8")
                self.usage["cached"] += 1
                txt = resp_f.read_text(encoding="utf-8")
                self.usage["output_chars"] += len(txt)
                return txt

        prompt_f.write_text(prompt_text, encoding="utf-8")
        if self.provider == "manual":
            pend = self.dir / "pending"
            pend.mkdir(exist_ok=True)
            (pend / f"{stem}.prompt.md").write_text(prompt_text, encoding="utf-8")
            self.pending.append(stem)
            return None

        t0 = time.time()
        txt = self._call(system, user, max_tokens)
        self.usage["calls"] += 1
        self.usage["output_chars"] += len(txt)
        resp_f.write_text(txt, encoding="utf-8")
        meta_f.write_text(json.dumps({"hash": h, "provider": self.preset, "model": self.model,
                                      "seconds": round(time.time() - t0, 1)}, indent=1))
        return txt

    def _call(self, system: str, user: str, max_tokens: int) -> str:
        last = None
        swapped = False
        for attempt in range(7):
            try:
                if self.provider == "anthropic":
                    return self._anthropic(system, user, max_tokens)
                return self._openai(system, user, max_tokens)
            except ImportError:
                raise
            except Exception as e:
                resp = getattr(e, "response", None)
                body = (getattr(resp, "text", "") or "")[:300] if resp is not None else ""
                if body:
                    e = RuntimeError(f"{e} | {body}")
                    e.response = resp
                last = e
                msg = str(e).lower()
                status = getattr(getattr(e, "response", None), "status_code", None)
                short = re.sub(r"\s+", " ", body or str(e))[:160]
                # model renamed / retired, or no free quota left for it: ask the endpoint which models exist, switch once
                no_quota = status == 429 and any(k in msg for k in ("limit: 0", "per day", "perday", "free_tier"))
                if (status == 404 or no_quota or "model" in msg and ("not found" in msg or "not supported" in msg))                         and self.provider == "openai" and not swapped:
                    swapped = True
                    alt = self._pick_model(exclude=self.model, prefer_lite=no_quota)
                    if alt and alt != self.model:
                        self.log(f"model {self.model} unavailable ({status}); switching to {alt}")
                        self.model = alt
                        continue
                if no_quota:
                    raise RuntimeError(f"LLM free-tier quota used up for {self.model}: {short}") from e
                if status in (401, 403) or any(s in msg for s in ("api key", "authentication", "permission")):
                    raise RuntimeError(f"LLM call failed (check the API key): {e}") from e
                if status in (400, 404) or "not found" in msg:
                    raise RuntimeError(f"LLM call failed: {e}") from e
                # rate limit / overload / network: back off (free tiers allow ~10-15 calls a minute)
                wait = 5 * 2 ** min(attempt, 4)
                ra = getattr(resp, "headers", {}) or {}
                try:
                    wait = max(wait, float(ra.get("retry-after", 0)))
                except (TypeError, ValueError):
                    pass
                wait = min(wait, 90)
                self.log(f"{self.model} busy ({status or type(e).__name__}): retry {attempt + 1}/6 in {wait:.0f}s - {short}")
                time.sleep(wait)
        raise RuntimeError(f"LLM call failed after retries: {last}")

    def _pick_model(self, exclude: str | None = None, prefer_lite: bool = False) -> str | None:
        """Pick a current model from the endpoint's /models list (prefers a non-lite 'flash' model on Gemini)."""
        import requests
        try:
            r = requests.get(f"{self.base_url.rstrip('/')}/models",
                             headers={"Authorization": f"Bearer {self.api_key}"}, timeout=30)
            r.raise_for_status()
            ids = [m["id"].split("/")[-1] for m in r.json().get("data", [])]
        except Exception:
            return None
        if self.preset == "gemini":
            flash = [i for i in ids if "flash" in i and i != exclude and not any(
                k in i for k in ("image", "tts", "live", "audio", "embed"))]
            if not prefer_lite:
                flash = [i for i in flash if "lite" not in i] or flash
            stable = [i for i in flash if "preview" not in i and "exp" not in i]
            pool = stable or flash
            ver = lambda i: [int(x) for x in re.split(r"[-.]", i) if x.isdigit()]
            return sorted(pool, key=ver, reverse=True)[0] if pool else None
        ids = [i for i in ids if i != exclude]
        return ids[0] if ids else None

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        import anthropic  # pip install anthropic
        client = anthropic.Anthropic(api_key=None if self.api_key == "none" else self.api_key)
        with client.messages.stream(model=self.model, max_tokens=max_tokens, system=system,
                                    messages=[{"role": "user", "content": user}]) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError("model refused")
        return "".join(b.text for b in msg.content if b.type == "text")

    def _openai(self, system: str, user: str, max_tokens: int) -> str:
        import requests
        if self.preset == "gemini":   # Gemini 2.5+ thinks before answering; thinking tokens count against max_tokens
            max_tokens = max(max_tokens, 32000)
        r = requests.post(f"{self.base_url.rstrip('/')}/chat/completions",
                          headers={"Authorization": f"Bearer {self.api_key}"},
                          json={"model": self.model, "temperature": self.temperature, "max_tokens": max_tokens,
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]},
                          timeout=300)
        r.raise_for_status()
        return r.json()["choices"][0]["message"].get("content") or ""


def parse_json(text: str):
    """Extract the first JSON object/array from a model reply (tolerates ```json fences and chatter)."""
    if text is None:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    cand = m.group(1) if m else text
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = cand.find(opener), cand.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(cand[i:j + 1])
            except json.JSONDecodeError:
                continue
    return None
