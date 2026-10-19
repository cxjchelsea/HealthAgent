"""Single Agent context selection: deterministic bounded role-safe history."""
from types import SimpleNamespace
import pytest
from agent.context import build_context
def msg(role,content,status="completed"):
    return SimpleNamespace(role=role,content=content,status=status)
def test_bounded_selection_and_roles():
    history=[msg("user","old"),msg("assistant","prior"),msg("tool","evil"),
             msg("user","ignored","provider_error"),msg("user","new"),msg("assistant","recent")]
    assert build_context(history,"question",max_messages=3,max_chars=22)==[
        {"role":"assistant","content":"prior"},{"role":"user","content":"new"},
        {"role":"assistant","content":"recent"}]
    assert build_context(history,"question",max_messages=3,max_chars=13)==[
        {"role":"assistant","content":"recent"}]
    with pytest.raises(ValueError):
        build_context(history,"too long",max_messages=10,max_chars=3)
