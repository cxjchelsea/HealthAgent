"""Typed, allowlisted, deterministic S2-A tools. No network or host command access."""
import ast
import operator
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError

class StrictToolArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')

class CalcArgs(StrictToolArgs):
    expression: str = Field(min_length=1, max_length=160)

class TaskArgs(StrictToolArgs):
    title: str = Field(min_length=1, max_length=120)

class EmptyArgs(StrictToolArgs):
    pass

SCHEMAS: dict[str, type[BaseModel]] = {
    "calculate": CalcArgs,
    "utc_time": EmptyArgs,
    "create_task": TaskArgs,
    "list_tasks": EmptyArgs,
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "calculate", "description": "Calculate a simple numeric arithmetic expression (+,-,*,/,parentheses).",
        "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "utc_time", "description": "Read the current time in UTC.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "create_task", "description": "Create a task in the current conversation (persistent across messages).",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "list_tasks", "description": "List persistent tasks in the current conversation.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
]

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

def safe_calculate(expression: str) -> float | int:
    parsed = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(parsed)) > 30:
        raise ValueError("Expression is too complex")
    def evaluate(node, depth=0):
        if depth > 12:
            raise ValueError("Expression depth exceeded")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(node.value) > 1e9:
                raise ValueError("Number too large")
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            result = OPS[type(node.op)](evaluate(node.left, depth + 1), evaluate(node.right, depth + 1))
            if isinstance(result, (int, float)) and abs(result) <= 1e12:
                return result
            raise ValueError("Result too large")
        if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
            return UNARY[type(node.op)](evaluate(node.operand, depth + 1))
        raise ValueError("Unsupported arithmetic syntax")
    try:
        return evaluate(parsed.body)
    except ZeroDivisionError as exc:
        raise ValueError("Division by zero") from exc

def execute_tool(name: str, arguments: Any, tasks: list[str], clock=None, task_store=None) -> dict:
    """Returns typed observation; errors become data for the model, not unhandled side effects."""
    if name not in SCHEMAS:
        return {"ok": False, "error": "unknown_tool"}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "invalid_arguments"}
    try:
        parsed = SCHEMAS[name].model_validate(arguments)
        if name == "calculate":
            return {"ok": True, "value": safe_calculate(parsed.expression)}
        if name == "utc_time":
            current = clock() if clock else datetime.now(timezone.utc)
            return {"ok": True, "utc": current.astimezone(timezone.utc).isoformat()}
        if name == "create_task":
            if task_store is not None:
                return task_store.create(parsed.title)
            if len(tasks) >= 20:
                return {"ok": False, "error": "task_limit"}
            tasks.append(parsed.title)
            return {"ok": True, "task_id": len(tasks), "title": parsed.title}
        if name == "list_tasks":
            if task_store is not None:
                return task_store.list()
            return {"ok": True, "tasks": [{"task_id": i + 1, "title": t} for i, t in enumerate(tasks)]}
    except (ValidationError, ValueError, SyntaxError, OverflowError, TypeError) as exc:
        return {"ok": False, "error": "invalid_arguments", "kind": type(exc).__name__}
    return {"ok": False, "error": "not_implemented"}
