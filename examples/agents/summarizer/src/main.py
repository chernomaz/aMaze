"""
summarizer-agent — calls the LLM once to summarize the input task.

Demonstrates the simplest possible agent→LLM→output flow.
The LLM call is routed through the aMaze proxy, which enforces policy and
graph step ordering before forwarding to the actual LLM endpoint.

Expected execution graph: simple-llm (step 1: llm_call → step 2: terminal)
"""

import os

import httpx
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

# ─── LLM config ───────────────────────────────────────────────────────────────
# Defaults point to the shared Qwen endpoint. Override via agent env_vars in DB.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://95.173.102.50:8000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "RBAC-LLM")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-32B-Instruct")

# Proxy URL injected by the Orchestrator — all outbound HTTP goes through it
PROXY_URL = os.getenv("AMAZE_PROXY_URL") or os.getenv("HTTP_PROXY", "")

app = FastAPI(title="summarizer-agent")


def make_llm_client() -> OpenAI:
    """Create an OpenAI-compatible client that routes through the aMaze proxy."""
    http_client = httpx.Client(proxy=PROXY_URL, timeout=120) if PROXY_URL else None
    return OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, http_client=http_client)


class InvokeRequest(BaseModel):
    task: str
    context: dict = {}
    input_files: list = []
    timeout_seconds: int = 300


class InvokeResponse(BaseModel):
    output: str
    tokens_used: int = 0
    status: str = "success"


@app.get("/health")
def health():
    return {"status": "ok", "agent": "summarizer-agent"}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest) -> InvokeResponse:
    client = make_llm_client()
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a concise summarization assistant. "
                        "Summarize the provided text clearly and briefly. "
                        "Preserve key facts, numbers, and conclusions."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Please summarize the following:\n\n{req.task}",
                },
            ],
            max_tokens=800,
            temperature=0.3,
        )
        output = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
        return InvokeResponse(output=output, tokens_used=tokens)
    except Exception as exc:
        return InvokeResponse(output=f"[summarizer error] {exc}", status="error")
    finally:
        client.close()
