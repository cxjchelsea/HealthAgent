"""Conservative source grounding for concrete tool parameters.

This is *not* an intent keyword classifier and does not grant permission:
it only rejects new concrete tool values with no literal user provenance.
Ambiguous in-context mentions still require semantic understanding (S25-01).
"""
import unicodedata


REQUIRED_USER_VALUES = {
    "create_task": ("title", "请告诉我要创建的任务名称或具体内容。"),
    "mcp_reference_lookup": ("term", "请告诉我需要查询的具体参考词条。"),
}


def user_supplied_value(tool: str, arguments: object, request: str) -> bool:
    if tool not in REQUIRED_USER_VALUES:
        return True
    field, _ = REQUIRED_USER_VALUES[tool]
    if not isinstance(arguments, dict):
        return False
    value = arguments.get(field)
    if not isinstance(value, str) or not value.strip():
        return False
    normalized_value = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized_request = unicodedata.normalize("NFKC", request).casefold()
    if not normalized_value:
        return False
    # Exact value sourcing is a deliberately restrictive admission guard.
    # Never treat a model-synthesized title/term as user-supplied.
    return normalized_value in normalized_request


def clarification_question(tool: str) -> str:
    return REQUIRED_USER_VALUES[tool][1]
