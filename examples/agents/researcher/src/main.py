"""
researcher-agent — LLM → web-search MCP (up to 3x) → LLM synthesis.

Demonstrates the research-loop execution graph:
  Step 1: llm_call  (generate search queries)
  Step 2: mcp_call  (websearch-mcp.search, max_loops=3)
  Step 3: llm_call  (synthesize results)
  Step 4: terminal

Each outbound call is validated by the aMaze proxy against the graph step
and policy limits before being forwarded.
"""

import os

import httpx
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

from agent_runtime import MCPClient

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://95.173.102.50:8000/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "RBAC-LLM")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-32B-Instruct")
PROXY_URL = os.getenv("AMAZE_PROXY_URL") or os.getenv("HTTP_PROXY", "")
MAX_SEARCHES = int(os.getenv("MAX_SEARCHES", "3"))

app = FastAPI(title="researcher-agent")


def make_llm_client() -> OpenAI:
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
    return {"status": "ok", "agent": "researcher-agent"}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest) -> InvokeResponse:
    client = make_llm_client()
    total_tokens = 0

    try:
        # ── Step 1: Generate search queries via LLM ────────────────────────────
        plan_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a research planner. Given a question or topic, "
                        f"generate up to {MAX_SEARCHES} concise search queries that will "
                        "retrieve the most relevant information. "
                        "Return ONLY the queries, one per line, no numbering or extra text."
                    ),
                },
                {"role": "user", "content": req.task},
            ],
            max_tokens=200,
            temperature=0.4,
        )
        queries_text = plan_resp.choices[0].message.content or ""
        queries = [q.strip() for q in queries_text.strip().splitlines() if q.strip()]
        queries = queries[:MAX_SEARCHES]
        total_tokens += plan_resp.usage.total_tokens if plan_resp.usage else 0

        # ── Step 2: Search for each query via MCP ─────────────────────────────
        search_results: list[str] = []
        mcp = MCPClient()
        try:
            for query in queries:
                try:
                    result = mcp.call("websearch-mcp.search", query=query, n_results=3)
                    results_list = result.get("results", [])
                    formatted = "\n".join(
                        f"  • {r['title']}: {r['snippet']}" for r in results_list
                    )
                    search_results.append(f"Query: {query}\n{formatted}")
                except Exception as e:
                    search_results.append(f"Query: {query}\n  [search failed: {e}]")
        finally:
            mcp.close()

        # ── Step 3: Synthesize results via LLM ────────────────────────────────
        search_context = "\n\n".join(search_results) if search_results else "No search results."
        synth_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a research synthesizer. Given search results, "
                        "produce a well-structured, comprehensive answer to the original question. "
                        "Cite sources by title where relevant. Be factual and concise."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Original question: {req.task}\n\n"
                        f"Search results:\n{search_context}\n\n"
                        "Please synthesize a comprehensive answer."
                    ),
                },
            ],
            max_tokens=1500,
            temperature=0.3,
        )
        output = synth_resp.choices[0].message.content or ""
        total_tokens += synth_resp.usage.total_tokens if synth_resp.usage else 0

        return InvokeResponse(output=output, tokens_used=total_tokens)

    except Exception as exc:
        return InvokeResponse(
            output=f"[researcher error] {exc}",
            tokens_used=total_tokens,
            status="error",
        )
    finally:
        client.close()
