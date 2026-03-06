"""
counter-mcp — stateful in-memory counter, ideal for testing max_loops enforcement.

Each call to increment increases a named counter by 1. The graph enforcer will
block further calls once the step's max_loops limit is reached, making this tool
perfect for verifying that the aMaze proxy correctly enforces loop caps.

Tools:
  counter-mcp.increment — input: {name: str = "default"}
  counter-mcp.get       — input: {name: str = "default"}
"""

from fastapi import FastAPI
from mcp_runtime.bootstrap import auto_register
from pydantic import BaseModel

app = FastAPI(title="counter-mcp")

# In-memory store — resets when the container restarts
_counters: dict[str, int] = {}


@app.on_event("startup")
def startup() -> None:
    auto_register(
        capabilities=[
            {
                "name": "counter-mcp",
                "capability_type": "mcp_server",
                "description": "Stateful in-memory counter — ideal for testing max_loops enforcement",
                "tags": ["counter", "stateful", "test"],
            },
            {
                "name": "counter-mcp.increment",
                "capability_type": "mcp_tool",
                "description": "Increment a named counter by 1 and return the new value",
                "tags": ["counter"],
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "default": "default"}},
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "value": {"type": "integer"}},
                },
            },
            {
                "name": "counter-mcp.get",
                "capability_type": "mcp_tool",
                "description": "Get the current value of a named counter (0 if never incremented)",
                "tags": ["counter"],
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "default": "default"}},
                },
            },
        ]
    )


class CallRequest(BaseModel):
    tool: str
    input: dict = {}


@app.get("/health")
def health():
    return {"status": "ok", "tool": "counter-mcp", "counters": len(_counters)}


@app.get("/counters")
def list_counters():
    """Debug endpoint to inspect all counters."""
    return {"counters": dict(_counters)}


@app.post("/call")
def call(req: CallRequest) -> dict:
    name = str(req.input.get("name", "default"))

    if req.tool == "counter-mcp.increment":
        _counters[name] = _counters.get(name, 0) + 1
        return {
            "result": {"name": name, "value": _counters[name], "incremented": True},
            "error": None,
        }

    elif req.tool == "counter-mcp.get":
        return {
            "result": {"name": name, "value": _counters.get(name, 0)},
            "error": None,
        }

    else:
        return {"result": None, "error": f"Unknown tool: {req.tool!r}"}
