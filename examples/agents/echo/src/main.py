"""
echo-agent — returns the input task verbatim.

No LLM calls. Used to test routing, graph enforcement, and policy pass-through
without burning any token budget.
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="echo-agent")


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
    return {"status": "ok", "agent": "echo-agent"}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(req: InvokeRequest) -> InvokeResponse:
    """Return the task text verbatim, with any context keys appended."""
    output = req.task
    if req.context:
        extras = ", ".join(f"{k}={v}" for k, v in req.context.items())
        output = f"{req.task}\n[context: {extras}]"
    return InvokeResponse(output=output)
