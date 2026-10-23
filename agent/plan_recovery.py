"""Verified, bounded recovery candidates for read-only planned tool steps.

Strategies generate *proposals*, never mark steps completed. The caller must
execute the candidate and verify its observation against the original goal.
"""
import ast
from dataclasses import dataclass
from agent.arithmetic_recovery import arithmetic_equivalent

@dataclass(frozen=True)
class RecoveryCandidate:
    tool: str
    arguments: dict
    strategy: str


def candidate_for_failure(tool: str, args: dict, error: str) -> RecoveryCandidate | None:
    """A bounded generic transformation for unsupported integer exponentiation."""
    if tool != "calculate" or error != "unsupported_expression":
        return None
    expression = args.get("expression")
    if not isinstance(expression, str) or len(expression) > 160:
        return None
    try:
        root = ast.parse(expression, mode="eval")
        if not (isinstance(root.body, ast.BinOp) and isinstance(root.body.op, ast.Pow)):
            return None
        base, exponent = root.body.left, root.body.right
        if (not isinstance(base, ast.Constant) or type(base.value) is not int
                or not isinstance(exponent, ast.Constant)
                or type(exponent.value) is not int):
            return None
        if abs(base.value) > 1000 or not 2 <= exponent.value <= 12:
            return None
        proposed = "*".join([f"({base.value})"] * exponent.value)
        if len(proposed) > 160:
            return None
        return RecoveryCandidate("calculate", {"expression": proposed}, "integer_power_expansion")
    except (ValueError, SyntaxError, TypeError):
        return None


def validates_recovery(original_args: dict, candidate: RecoveryCandidate,
                       result: dict) -> bool:
    return (candidate.tool == "calculate" and result.get("ok") is True
            and arithmetic_equivalent(original_args.get("expression"),
                                     candidate.arguments.get("expression"),
                                     result.get("value")))
