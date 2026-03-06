"""
calculator-mcp — safe math expression evaluator using Python's AST.

No use of eval() or exec(). Only numeric literals and a whitelist of
arithmetic operators are allowed. Raises an error for any other AST node.

Tool:
  calculator-mcp.calculate — input: {expression: str}
                             output: {expression: str, value: float}
"""

import ast
import math
import operator
from typing import Any

from fastapi import FastAPI
from mcp_runtime.bootstrap import auto_register
from pydantic import BaseModel

app = FastAPI(title="calculator-mcp")


@app.on_event("startup")
def startup() -> None:
    auto_register(
        capabilities=[
            {
                "name": "calculator-mcp",
                "capability_type": "mcp_server",
                "description": "Safe arithmetic expression evaluator (no eval, AST-based)",
                "tags": ["math", "calculator"],
            },
            {
                "name": "calculator-mcp.calculate",
                "capability_type": "mcp_tool",
                "description": "Evaluate a math expression safely. Supports +, -, *, /, **, %, //.",
                "tags": ["math"],
                "input_schema": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "expression": {"type": "string"},
                        "value": {"type": "number"},
                    },
                },
            },
        ]
    )


# Whitelist of safe binary operators
_BINOPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

# Whitelist of safe unary operators
_UNOPS: dict[type, Any] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Whitelisted math functions (name → callable)
_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    if isinstance(node, ast.BinOp):
        op_fn = _BINOPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return float(op_fn(left, right))

    if isinstance(node, ast.UnaryOp):
        op_fn = _UNOPS.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return float(op_fn(_eval_node(node.operand)))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only simple function calls are allowed")
        fn = _FUNCTIONS.get(node.func.id)
        if fn is None:
            raise ValueError(f"Unknown function: {node.func.id!r}")
        if isinstance(fn, float):  # constant like pi, e
            return fn
        args = [_eval_node(a) for a in node.args]
        return float(fn(*args))

    if isinstance(node, ast.Name):
        fn = _FUNCTIONS.get(node.id)
        if fn is None:
            raise ValueError(f"Unknown name: {node.id!r}")
        if isinstance(fn, float):
            return fn
        raise ValueError(f"{node.id!r} is a function, not a constant")

    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def safe_calculate(expression: str) -> float:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Syntax error in expression: {exc}") from exc
    return _eval_node(tree.body)


class CallRequest(BaseModel):
    tool: str
    input: dict = {}


@app.get("/health")
def health():
    return {"status": "ok", "tool": "calculator-mcp"}


@app.post("/call")
def call(req: CallRequest) -> dict:
    if req.tool != "calculator-mcp.calculate":
        return {"result": None, "error": f"Unknown tool: {req.tool!r}"}

    expression = req.input.get("expression", "").strip()
    if not expression:
        return {"result": None, "error": "Missing required input: 'expression'"}

    try:
        value = safe_calculate(expression)

        # Detect non-finite results
        if not math.isfinite(value):
            return {"result": None, "error": f"Result is not finite: {value}"}

        # Return int if the value is a whole number, for cleaner output
        display = int(value) if value == int(value) and abs(value) < 1e15 else value
        return {"result": {"expression": expression, "value": display}, "error": None}

    except ZeroDivisionError:
        return {"result": None, "error": "Division by zero"}
    except ValueError as exc:
        return {"result": None, "error": str(exc)}
    except Exception as exc:
        return {"result": None, "error": f"Evaluation failed: {exc}"}
