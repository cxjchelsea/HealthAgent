"""Narrow, fail-closed withdrawal veto for a Pending task write.

This is a safety restriction, never an intent classifier or an allow signal.
It blocks explicit withdrawals that would otherwise be interpreted as titles.
The patterns are intentionally incomplete; semantic coverage still needs
independent evaluation.
"""
import re
import unicodedata

_WITHDRAW = (
    re.compile(r"^(?:算了|不创建了|不建了|不添加了|不做了|不用了|"
               r"别创建了|别建了|取消这个待办|取消该任务)"
               r"(?:[，。,.!！\s]|$)"),
    re.compile(r"(?:待办|任务).{0,3}(?:不创建|不要创建|不用创建|别创建|"
               r"不建立|不新建|无需创建)"),
    re.compile(r"(?:不创建|不要创建|不用创建|别创建|不建立|不新建)"
               r".{0,4}(?:待办|任务)"),
    # An explicit cancellation of a prior task-creation request is a veto,
    # never positive evidence for an executable task title.
    re.compile(r"^(?:请|帮我)?(?:取消|撤销).{0,20}(?:待办|任务)"
               r".{0,20}(?:创建|新增)"),
    re.compile(r"^(?:never mind|cancel the task|do not create the task)"
               r"(?:[.,!\s]|$)"),
)


def explicitly_withdraws_pending_task_write(message: str) -> bool:
    """Conservative no-write veto; never changes or adds execution authority."""
    if not isinstance(message, str) or len(message) > 4096:
        return False
    value = unicodedata.normalize("NFKC", message).casefold().strip()
    return bool(value and any(p.search(value) for p in _WITHDRAW))

# This is a conservative *shape* check for direct title replies, not an
# intent recognizer. A full conversational instruction must not be treated
# as a task title merely because the model chose clarify_response.
# Ambiguous compound utterances require clarification rather than mutation.
_NON_TITLE_DELIMITERS = frozenset("，,。.!！?？；;：:") | {"\r", "\n"}


def standalone_pending_task_title(message: str) -> bool:
    """Permit only a short, single-phrase reply to an unanswered title prompt.

    This negative guard is deliberately not semantic proof that the user
    intends a task. The Primary proposal and durable permit are still needed.
    """
    if not isinstance(message, str):
        return False
    value = unicodedata.normalize("NFKC", message).strip()
    return bool(
        value and len(value) <= 80
        and not any(ch in _NON_TITLE_DELIMITERS for ch in value)
        and not explicitly_withdraws_pending_task_write(value)
    )
