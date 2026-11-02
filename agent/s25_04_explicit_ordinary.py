"""Explicit, narrow ordinary-operation selection; not an execution authorization.

Only current-turn canonical arithmetic commands may skip the M5 CREATE
*proposal*. No saved plan, pending clarification, or implicit language route.
All actual execution remains subject to the existing AgentLoop Host fences.
"""
import re

_ARITHMETIC = re.compile(r"[0-9]+(?:[.][0-9]+)?(?:[ \t]*[+*/()-][ \t]*[0-9()+*/. \t-]*)*")


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
    if len(expression) > 100 or not expression or not _ARITHMETIC.fullmatch(expression):
        return False
    return any(ch.isdigit() for ch in expression)
