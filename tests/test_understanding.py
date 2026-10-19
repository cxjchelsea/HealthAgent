from agent.understanding import understand

def test_unknown_requires_clarification():
    result = understand("帮我处理一下")
    assert result.goal_kind == "unknown"
    assert result.needs_clarification
    assert result.clarification_reason == "missing_target_or_referent"

def test_clear_information_goal():
    result = understand("请解释 Agent Loop")
    assert result.goal_kind == "information_request"
    assert not result.needs_clarification
    assert result.source == "rule_based_s1"
