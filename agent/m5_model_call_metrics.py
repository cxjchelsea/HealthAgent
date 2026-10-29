"""Request-local, bounded categorization of model HTTP attempts."""
from contextlib import contextmanager
from contextvars import ContextVar

model_call_category = ContextVar("model_call_category", default="other")
KNOWN_CATEGORIES = frozenset({"plan_intent", "plan_revision", "plan_cancellation",
                              "plan_execution_authorization", "plan_execution_scope",
                              "primary", "repair", "other"})


@contextmanager
def categorize_model_calls(category: str):
    if category not in KNOWN_CATEGORIES:
        raise ValueError("m5_unknown_model_call_category")
    token = model_call_category.set(category)
    try:
        yield
    finally:
        model_call_category.reset(token)
