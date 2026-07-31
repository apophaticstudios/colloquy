"""A tiny OpenAI-compatible stub that mimics Llama Guard, for offline testing
of the guardrail pipeline. Flags anything mentioning explosives/weapons as
unsafe (S9), everything else safe.

Run: uvicorn tests.stub_llm:app --port 9099
Then: LLM_API_BASE=http://127.0.0.1:9099 LLM_API_KEY=test uvicorn app.main:app ...
"""
from fastapi import FastAPI, Request

app = FastAPI()

BAD = ("explosive", "bomb", "detonat", "nerve agent", "bioweapon", "ransomware")


@app.post("/chat/completions")
async def chat(request: Request):
    body = await request.json()
    text = " ".join(m.get("content", "") for m in body.get("messages", [])).lower()
    if any(w in text for w in BAD):
        content = "unsafe\nS9"
    else:
        content = "safe"
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}
