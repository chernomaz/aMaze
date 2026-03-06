"""
filesystem-mcp — read_file, write_file, list_dir, all scoped to /workspace.

Exposes POST /call receiving {"tool": "<name>", "input": {...}}.
Self-registers with the aMaze Registry on startup and sends heartbeats.

Tools:
  filesystem-mcp.read_file   — input: {path: str}
  filesystem-mcp.write_file  — input: {path: str, content: str}
  filesystem-mcp.list_dir    — input: {path: str}
"""

import os
from pathlib import Path

from fastapi import FastAPI
from mcp_runtime.bootstrap import auto_register
from pydantic import BaseModel

WORKSPACE = Path(os.getenv("WORKSPACE_PATH", "/workspace"))

app = FastAPI(title="filesystem-mcp")


@app.on_event("startup")
def startup() -> None:
    auto_register(
        capabilities=[
            {
                "name": "filesystem-mcp",
                "capability_type": "mcp_server",
                "description": "File system operations (read, write, list) scoped to /workspace",
                "tags": ["filesystem", "files", "io"],
            },
            {
                "name": "filesystem-mcp.read_file",
                "capability_type": "mcp_tool",
                "description": "Read a file from /workspace",
                "tags": ["filesystem", "read"],
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                "output_schema": {"type": "object", "properties": {"content": {"type": "string"}, "path": {"type": "string"}}},
            },
            {
                "name": "filesystem-mcp.write_file",
                "capability_type": "mcp_tool",
                "description": "Write a file to /workspace",
                "tags": ["filesystem", "write"],
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}},
            },
            {
                "name": "filesystem-mcp.list_dir",
                "capability_type": "mcp_tool",
                "description": "List directory contents under /workspace",
                "tags": ["filesystem", "list"],
                "input_schema": {"type": "object", "properties": {"path": {"type": "string", "default": "/"}}},
            },
        ]
    )


class CallRequest(BaseModel):
    tool: str
    input: dict = {}


def _safe_path(path: str) -> Path:
    """Resolve path and guarantee it stays within /workspace."""
    resolved = (WORKSPACE / path.lstrip("/")).resolve()
    if not str(resolved).startswith(str(WORKSPACE.resolve())):
        raise ValueError(f"Path escape attempt blocked: {path!r}")
    return resolved


@app.get("/health")
def health():
    return {"status": "ok", "tool": "filesystem-mcp"}


@app.post("/call")
def call(req: CallRequest) -> dict:
    tool = req.tool
    inp = req.input

    try:
        if tool == "filesystem-mcp.read_file":
            path = _safe_path(inp.get("path", ""))
            if not path.exists():
                return {"result": None, "error": f"File not found: {inp.get('path')}"}
            if not path.is_file():
                return {"result": None, "error": f"Not a file: {inp.get('path')}"}
            content = path.read_text(encoding="utf-8", errors="replace")
            return {"result": {"content": content, "path": str(path), "size": len(content)}, "error": None}

        elif tool == "filesystem-mcp.write_file":
            path = _safe_path(inp.get("path", ""))
            content = inp.get("content", "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"result": {"written": True, "path": str(path), "bytes": len(content.encode())}, "error": None}

        elif tool == "filesystem-mcp.list_dir":
            path = _safe_path(inp.get("path", "/"))
            if not path.exists():
                return {"result": {"entries": [], "path": str(path)}, "error": None}
            if not path.is_dir():
                return {"result": None, "error": f"Not a directory: {inp.get('path')}"}
            entries = [
                {
                    "name": e.name,
                    "type": "file" if e.is_file() else "dir",
                    "size": e.stat().st_size if e.is_file() else 0,
                }
                for e in sorted(path.iterdir())
            ]
            return {"result": {"entries": entries, "path": str(path), "count": len(entries)}, "error": None}

        else:
            return {"result": None, "error": f"Unknown tool: {tool!r}"}

    except ValueError as exc:
        return {"result": None, "error": str(exc)}
    except PermissionError as exc:
        return {"result": None, "error": f"Permission denied: {exc}"}
    except Exception as exc:
        return {"result": None, "error": f"Unexpected error: {exc}"}
