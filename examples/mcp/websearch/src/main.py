"""
websearch-mcp — returns hardcoded synthetic search results (offline-safe).

Designed for testing the research-loop graph without any real internet access.
Results are plausible but fictional. They incorporate the query text so the
downstream LLM synthesis step receives contextually relevant content.

Tool:
  websearch-mcp.search — input: {query: str, n_results: int = 3}
"""

import hashlib

from fastapi import FastAPI
from mcp_runtime.bootstrap import auto_register
from pydantic import BaseModel

app = FastAPI(title="websearch-mcp")


@app.on_event("startup")
def startup() -> None:
    auto_register(
        capabilities=[
            {
                "name": "websearch-mcp",
                "capability_type": "mcp_server",
                "description": "Web search — returns synthetic results (offline-safe for testing)",
                "tags": ["search", "web", "fake"],
            },
            {
                "name": "websearch-mcp.search",
                "capability_type": "mcp_tool",
                "description": "Search the web (returns synthetic results, no real network required)",
                "tags": ["search", "web"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "n_results": {"type": "integer", "default": 3},
                    },
                    "required": ["query"],
                },
            },
        ]
    )


class CallRequest(BaseModel):
    tool: str
    input: dict = {}


# Result templates — {topic} is replaced with the query
_TEMPLATES = [
    {
        "title": "Overview of {topic}",
        "url": "https://encyclopedia.example.com/{slug}",
        "snippet": (
            "{topic} is a well-established concept in modern computing. "
            "Key aspects include scalability, reliability, and performance optimization. "
            "Practitioners typically follow established best practices to achieve production-grade results."
        ),
    },
    {
        "title": "{topic}: A Practical Guide",
        "url": "https://docs.example.org/guides/{slug}",
        "snippet": (
            "This guide covers the fundamentals of {topic} with hands-on examples. "
            "Topics include setup, configuration, common patterns, and troubleshooting. "
            "Suitable for both beginners and experienced practitioners."
        ),
    },
    {
        "title": "Advanced {topic} Patterns",
        "url": "https://engineering.example.com/blog/{slug}-patterns",
        "snippet": (
            "Experienced engineers share advanced techniques for {topic}. "
            "Covers edge cases, performance tuning, and architectural trade-offs. "
            "Includes real-world case studies from large-scale deployments."
        ),
    },
    {
        "title": "{topic} — Research Survey 2024",
        "url": "https://research.example.edu/survey/{slug}-2024",
        "snippet": (
            "A comprehensive survey of recent developments in {topic}. "
            "Reviews 42 papers from leading conferences. "
            "Identifies key open problems and promising research directions."
        ),
    },
    {
        "title": "Why {topic} Matters for Modern Systems",
        "url": "https://tech.example.net/{slug}-importance",
        "snippet": (
            "Analysis of why {topic} has become critical infrastructure in 2024. "
            "Examines adoption trends, business impact, and future outlook. "
            "Industry leaders weigh in on best practices and common pitfalls."
        ),
    },
]


def _slug(text: str) -> str:
    return text.lower().replace(" ", "-")[:40]


def _pick_templates(query: str, n: int) -> list[dict]:
    """Deterministically pick n templates based on query hash (reproducible results)."""
    h = int(hashlib.md5(query.encode()).hexdigest(), 16)
    indices = [(h + i * 7) % len(_TEMPLATES) for i in range(min(n, len(_TEMPLATES)))]
    seen = set()
    unique = []
    for idx in indices:
        if idx not in seen:
            seen.add(idx)
            unique.append(_TEMPLATES[idx])
    return unique


@app.get("/health")
def health():
    return {"status": "ok", "tool": "websearch-mcp"}


@app.post("/call")
def call(req: CallRequest) -> dict:
    if req.tool != "websearch-mcp.search":
        return {"result": None, "error": f"Unknown tool: {req.tool!r}"}

    query = req.input.get("query", "")
    if not query:
        return {"result": None, "error": "Missing required input: 'query'"}

    n_results = max(1, min(int(req.input.get("n_results", 3)), 5))
    slug = _slug(query)
    templates = _pick_templates(query, n_results)

    results = [
        {
            "title": t["title"].format(topic=query, slug=slug),
            "url": t["url"].format(topic=query, slug=slug),
            "snippet": t["snippet"].format(topic=query, slug=slug),
        }
        for t in templates
    ]

    return {
        "result": {
            "query": query,
            "results": results,
            "total": len(results),
            "note": "Synthetic results — offline-safe for testing.",
        },
        "error": None,
    }
