"""C01/C02: typed arithmetic observation is the source of the failure explanation."""
from agent.tools import execute_tool
from agent.tools import safe_calculate
import pytest


@pytest.mark.parametrize("expression,code", [
    ("10/0", "division_by_zero"),
    ("2**10", "unsupported_expression"),
    ("1 +", "unsupported_expression"),
])
def test_calculate_distinct_failure_codes(expression, code):
    observed = execute_tool("calculate", {"expression": expression}, [])
    assert observed["ok"] is False
    assert observed["error"] == code
    assert observed["retryable"] is False
    assert observed["message"]
    assert "value" not in observed


def test_calculation_schema_error_stays_invalid_arguments():
    observed = execute_tool("calculate", {"expression": ""}, [])
    assert observed["ok"] is False
    assert observed["error"] == "invalid_arguments"


def test_successful_calculation_unchanged():
    assert execute_tool("calculate", {"expression": "(19+23)*4"}, []) == {
        "ok": True, "value": 168,
    }


def test_safe_calculate_still_rejects_zero_division():
    with pytest.raises(ZeroDivisionError):
        safe_calculate("10/0")
