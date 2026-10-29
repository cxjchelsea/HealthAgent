"""Scoped model-call attribution must not leak between reviewers."""
import asyncio
import pytest
from agent.m5_model_call_metrics import (
    attributed, categorize_model_calls, model_call_category,
)


def test_model_call_category_nested_reset():
    assert model_call_category.get() == "other"
    with categorize_model_calls("plan_intent"):
        assert model_call_category.get() == "plan_intent"
        with categorize_model_calls("repair"):
            assert model_call_category.get() == "repair"
        assert model_call_category.get() == "plan_intent"
    assert model_call_category.get() == "other"
    with pytest.raises(ValueError, match="m5_unknown_model_call_category"):
        with categorize_model_calls("unknown"):
            pass


@pytest.mark.asyncio
async def test_attribution_decorator_scoped_per_async_task():
    @attributed("plan_revision")
    async def reviewer():
        await asyncio.sleep(0)
        return model_call_category.get()

    result = await asyncio.gather(reviewer(), asyncio.sleep(0, result=model_call_category.get()))
    assert result == ["plan_revision", "other"]
    assert model_call_category.get() == "other"
