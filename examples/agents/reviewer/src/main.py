"""
reviewer-agent — reads a file via filesystem MCP tool, then calls LLM to review it.

Demonstrates the summarize-and-review execution graph:
  Step 1: llm_call  (optional: planning pass)
  Step 2: mcp_call  (filesystem-mcp.read_file)
  Step 3: llm_call  (review the file content)
  Step 4: terminal

Task format expected by this agent:
  "file: /workspace/path/to/file.txt
  Review for: security issues, code quality, and correctness."

If no "file:" prefix is given, the task itself is reviewed as raw text.
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

app = FastAPI(title="reviewer-agent")


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


def _parse_task(task: str) -> tuple[str | None, str]:
    """Extract optional file path and review instructions from task string."""
    lines = task.strip().splitlines()
    if lines and lines[0].lower().startswith("file:"):
        file_path = lines[0][5:].strip()
        instructions = "\n".join(lines[1:]).strip()
        if not instructions:
            instructions = "Review this file thoroughly. Note any issues with code quality, correctness, security, and style."
        return file_path, instructions
    return None, task


@app.get("/health")
def health():
    return {"status": "ok", "agent": "reviewer-agent"}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest) -> InvokeResponse:
    client = make_llm_client()
    total_tokens = 0

    try:
        file_path, review_instructions = _parse_task(req.task)

        # ── Step 1: Read file via MCP (if file path given) ────────────────────
        file_content = ""
        file_label = "the provided text"

        if file_path:
            mcp = MCPClient()
            try:
                result = mcp.call("filesystem-mcp.read_file", path=file_path)
                file_content = result.get("content", str(result))
                file_label = file_path
            except Exception as e:
                file_content = f"[Could not read file: {e}]"
            finally:
                mcp.close()
        else:
            # No file path — review the task text itself
            file_content = req.task
            review_instructions = "Review the following text for quality, clarity, and correctness."

        # ── Step 2: LLM review ────────────────────────────────────────────────
        user_prompt = (
            f"File: {file_label}\n\n"
            f"Content:\n```\n{file_content}\n```\n\n"
            f"Review instructions: {review_instructions}"
        )

        review_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a thorough and constructive code and document reviewer. "
                        "Provide detailed, actionable feedback organized by category. "
                        "Be specific about line numbers or sections when possible. "
                        "Always suggest concrete improvements."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=2000,
            temperature=0.2,
        )
        output = review_resp.choices[0].message.content or ""
        total_tokens = review_resp.usage.total_tokens if review_resp.usage else 0

        return InvokeResponse(output=output, tokens_used=total_tokens)

    except Exception as exc:
        return InvokeResponse(
            output=f"[reviewer error] {exc}",
            tokens_used=total_tokens,
            status="error",
        )
    finally:
        client.close()
