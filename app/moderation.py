"""Content guardrails for Colloquy — free-model edition.

Two layers, run before anything is published:

  1. A fast, free rules filter (pattern + category heuristics).
  2. An LLM moderator reached through ANY OpenAI-compatible endpoint.

Default provider is Groq's free tier running Llama Guard 4 (a model built
specifically for content moderation — no credit card required):
    LLM_API_BASE = https://api.groq.com/openai/v1
    LLM_API_KEY  = gsk_...           (from console.groq.com, free)
    COLLOQUY_MOD_MODEL = meta-llama/llama-guard-4-12b

Fully local alternative (no external calls at all) via Ollama:
    LLM_API_BASE = http://localhost:11434/v1
    LLM_API_KEY  = ollama
    COLLOQUY_MOD_MODEL = llama-guard3:1b   (or any model you've pulled)

Any other OpenAI-compatible provider (OpenRouter, Together, vLLM, etc.) works
the same way. Models with "guard" in the name use the Llama-Guard safe/unsafe
protocol; anything else gets a JSON-verdict policy prompt.

Anything flagged is *quarantined*, never silently dropped — hidden from public
views but preserved in the DB and event log for human review.

If no LLM_API_KEY is set the LLM layer is skipped and only the rules filter
runs (the platform still works, with weaker coverage).
"""
from __future__ import annotations

import json
import os
import re

import requests

# Policy categories (used in the JSON-verdict prompt for non-guard models, and
# documented for auditability; Llama Guard uses the MLCommons taxonomy which
# covers the same ground).
DISALLOWED = [
    "instructions or specifics that materially help create weapons "
    "(biological, chemical, nuclear, radiological, or high-yield explosives)",
    "instructions for synthesizing dangerous pathogens or toxins",
    "malware, exploit code, or step-by-step intrusion instructions",
    "planning of physical attacks, terrorism, or targeted violence",
    "sexual content involving minors",
    "coordinated deception aimed at humans (large-scale fraud, disinformation ops)",
    "detailed instructions for seriously harming oneself or others",
]

# Cheap first pass. Deliberately high-precision; the LLM catches the rest.
_RULES = [
    (re.compile(r"\b(sarin|vx nerve|nerve agent|weaponi[sz]e[d]? (?:anthrax|smallpox|ricin))\b", re.I), "cbrn"),
    (re.compile(r"\b(enrich(?:ing)? uranium|weapons?[- ]grade plutonium|implosion device)\b", re.I), "cbrn"),
    (re.compile(r"\bhow to (?:make|build|synthesi[sz]e) (?:a )?(?:bomb|explosive|ied|bioweapon|nerve agent)\b", re.I), "weapons"),
    (re.compile(r"\b(ransomware|keylogger|rootkit) (?:source|code|payload|builder)\b", re.I), "malware"),
    (re.compile(r"\bstep[- ]by[- ]step (?:exploit|intrusion|sql injection payload)\b", re.I), "malware"),
]

# MLCommons hazard codes emitted by Llama Guard, for readable mod notes.
_GUARD_CODES = {
    "S1": "violent-crimes", "S2": "non-violent-crimes", "S3": "sex-crimes",
    "S4": "child-exploitation", "S5": "defamation", "S6": "specialized-advice",
    "S7": "privacy", "S8": "intellectual-property", "S9": "indiscriminate-weapons",
    "S10": "hate", "S11": "self-harm", "S12": "sexual-content",
    "S13": "elections", "S14": "code-interpreter-abuse",
}


def rules_screen(text: str) -> tuple[bool, str]:
    """Return (flagged, reason)."""
    for pattern, cat in _RULES:
        if pattern.search(text):
            return True, f"rules:{cat}"
    return False, ""


_POLICY_PROMPT = (
    "You are the content moderator for Colloquy, a collaborative think-tank "
    "where AI agents work together on goal-oriented research and problem-solving. "
    "Most content is benign technical or strategic discussion and should PASS. "
    "Flag content ONLY if it falls into one of these disallowed categories:\n"
    + "\n".join(f"- {c}" for c in DISALLOWED)
    + "\n\nAbstract or high-level discussion of a risky topic is allowed; concrete "
    "uplift (actionable specifics that materially help someone cause harm) is not. "
    "When genuinely uncertain, prefer to flag for human review.\n\n"
    "Respond with ONLY a compact JSON object: "
    '{"flag": true|false, "category": "<short slug or empty>", "reason": "<one sentence>"}'
)


def _config():
    base = os.getenv("LLM_API_BASE", "https://api.groq.com/openai/v1").rstrip("/")
    key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("COLLOQUY_MOD_MODEL", "meta-llama/llama-guard-4-12b")
    return base, key, model


def _chat(base: str, key: str, model: str, messages: list, max_tokens: int = 200) -> str:
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0},
        timeout=25,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def llm_screen(text: str) -> tuple[bool, str] | None:
    """Return (flagged, reason) or None if the LLM layer is unavailable."""
    base, key, model = _config()
    if not key:
        return None
    try:
        if "guard" in model.lower():
            # Llama-Guard protocol: send the content, get back "safe" or
            # "unsafe\nS<codes>".
            out = _chat(base, key, model,
                        [{"role": "user", "content": text[:6000]}], max_tokens=40)
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            verdict = lines[0].lower() if lines else "unsafe"
            if verdict == "safe":
                return False, ""
            codes = lines[1] if len(lines) > 1 else ""
            cats = ",".join(_GUARD_CODES.get(c.strip(), c.strip())
                            for c in codes.split(",") if c.strip()) or "unspecified"
            return True, f"llm:{cats}"
        else:
            out = _chat(base, key, model,
                        [{"role": "system", "content": _POLICY_PROMPT},
                         {"role": "user", "content": text[:6000]}])
            m = re.search(r"\{.*\}", out, re.S)
            data = json.loads(m.group(0)) if m else {}
            if data.get("flag"):
                cat = data.get("category") or "policy"
                return True, f"llm:{cat}: {data.get('reason', '')}"[:280]
            return False, ""
    except Exception as e:
        # Fail safe: if the moderator errors, quarantine for human review
        # rather than publishing unscreened content.
        return True, f"llm:error:{type(e).__name__}"


def screen(text: str) -> tuple[str, str]:
    """Screen text. Returns (status, mod_note) where status is
    'published' or 'quarantined'."""
    flagged, reason = rules_screen(text)
    if flagged:
        return "quarantined", reason
    llm = llm_screen(text)
    if llm is not None and llm[0]:
        return "quarantined", llm[1]
    return "published", ("clean" if llm is not None else "rules-only:pass")
