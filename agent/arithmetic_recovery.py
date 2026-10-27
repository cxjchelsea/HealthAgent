"""Bounded proof of equivalence for arithmetic tool recovery.

Only exact rational arithmetic, a tiny operator set and bounded exponentiation.
Never resolve unrelated failures or accept a model assertion as proof.
"""
import ast
from fractions import Fraction


def _value(node: ast.AST, depth: int = 0) -> Fraction:
    if depth > 12:
        raise ValueError("expression_depth")
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        result = Fraction(str(node.value))
        if abs(result) <= 10**9:
            return result
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        val = _value(node.operand, depth + 1)
        return val if isinstance(node.op, ast.UAdd) else -val
    elif isinstance(node, ast.BinOp):
        left, right = _value(node.left, depth + 1), _value(node.right, depth + 1)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        elif isinstance(node.op, ast.Div):
            result = left / right
        elif isinstance(node.op, ast.Pow):
            if right.denominator != 1 or not (0 <= right.numerator <= 16):
                raise ValueError("unsafe_exponent")
            result = left ** right.numerator
        else:
            raise ValueError("unsupported_expression")
        if abs(result.numerator) <= 10**15 and result.denominator <= 10**15:
            return result
    raise ValueError("unsafe_expression")


def arithmetic_equivalent(failed_expression: str, recovered_expression: str,
                          recovered_value: object) -> bool:
    if (not isinstance(failed_expression, str) or not isinstance(recovered_expression, str)
            or type(recovered_value) not in (int, float)):
        return False
    try:
        if max(len(failed_expression), len(recovered_expression)) > 160:
            return False
        left = ast.parse(failed_expression, mode="eval")
        right = ast.parse(recovered_expression, mode="eval")
        if max(sum(1 for _ in ast.walk(left)), sum(1 for _ in ast.walk(right))) > 30:
            return False
        calculated = _value(right.body)
        return (_value(left.body) == calculated
                and calculated == Fraction(str(recovered_value)))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return False
