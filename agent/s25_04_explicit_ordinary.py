"""Explicit ordinary calculation routing eligibility, never tool authorization."""
import ast
import re

_ALLOWED = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_NUMBER = re.compile(r"[0-9]+(?:[.][0-9]+)?\Z")


def _safe_arithmetic(expression: str) -> bool:
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return False

    def valid(node, depth: int = 0) -> bool:
        if depth > 16:
            return False
        if isinstance(node, ast.Expression):
            return valid(node.body, depth + 1)
        if isinstance(node, ast.BinOp):
            return (type(node.op) in _ALLOWED and
                    valid(node.left, depth + 1) and
                    valid(node.right, depth + 1))
        if isinstance(node, ast.UnaryOp):
            return type(node.op) in (ast.UAdd, ast.USub) and valid(node.operand, depth + 1)
        if isinstance(node, ast.Constant):
            return type(node.value) in (int, float) and bool(
                _NUMBER.fullmatch(ast.get_source_segment(expression, node) or ""))
        return False

    return valid(tree)


def admit_explicit_ordinary_calculation(*, selection: str | None,
                                        message: str, saved_plan_present: bool,
                                        pending_action_present: bool,
                                        clarification_present: bool) -> bool:
    if selection != "calculate" or saved_plan_present:
        return False
    if pending_action_present or clarification_present:
        return False
    prefix = "请计算 "
    if not message.startswith(prefix):
        return False
    expression = message[len(prefix):]
    if not expression or len(expression) > 100 or not expression.isascii():
        return False
    return _safe_arithmetic(expression)
