"""Static, trusted skill manifests; no arbitrary file paths or dynamic imports."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tool_names: tuple[str, ...]

SKILLS = (
    Skill("arithmetic", "Numerical calculations", ("calculate",)),
    Skill("clock", "UTC time lookup", ("utc_time",)),
    Skill("tasks", "Create and list conversation tasks", ("create_task", "list_tasks")),
    Skill("memory", "Store or recall consent-governed user memory", ("save_memory", "recall_memory")),
    Skill("reference", "Consult a trusted mock reference catalog", ("mcp_reference_lookup",)),
    Skill("planning", "Plan and revise multi-step tasks", ("propose_plan", "revise_plan", "complete_plan_step")),
)

def select_skills(goal: str, *, strategy: str = "dynamic") -> tuple[Skill, ...]:
    if strategy == "all":
        return SKILLS
    if strategy != "dynamic":
        raise ValueError("Unsupported skill strategy")
    # Routing is only tool-discovery policy, never a substitute for model tool choice.
    # Conservative fallback for ambiguous inputs avoids silently suppressing tools.
    text = goal.casefold()
    keywords = {
        "arithmetic": ("计算", "算一下", "多少", "calculate", "arithmetic", "乘", "除", "+", "*"),
        "clock": ("时间", "几点", "utc", "clock", "time"),
        "tasks": ("任务", "待办", "task", "todo", "清单"),
        "memory": ("记住", "记忆", "回忆", "保存", "memory", "remember", "recall"),
        "reference": ("参考", "资料", "词条", "术语", "reference", "catalog", "mcp", "rag"),
        "planning": ("计划", "规划", "多步", "步骤", "重新", "失败后", "plan", "replan", "workflow"),
    }
    matched = [skill for skill in SKILLS
               if any(word in text for word in keywords[skill.name])]
    return tuple(matched) if matched else SKILLS

def exposed_tools(goal: str, schemas: list[dict], *, strategy: str = "dynamic") -> list[dict]:
    names = {name for skill in select_skills(goal, strategy=strategy) for name in skill.tool_names}
    return [schema for schema in schemas if schema["function"]["name"] in names]

# Trusted, versioned instructions are not presented until their manifest is selected.
INSTRUCTIONS = {
    "arithmetic": "Use calculate for arithmetic; report observation, not a guessed number.",
    "clock": "Use utc_time to read the current UTC time before answering.",
    "tasks": "Use create_task/list_tasks; tasks belong to the current conversation.",
    "memory": "Use recall_memory for exact keys; saving requires current-turn consent.",
    "reference": "Use mcp_reference_lookup for terms present in the trusted reference catalog.",
    "planning": "Bind plan steps. On correction, set revise_plan.invalidate_step_ids for superseded completed IDs; preserve others. Revise failed steps.",
}
SKILL_VERSION = "s2c-skills-v1"

def load_instructions(goal: str, *, strategy: str = "dynamic",
                      max_chars: int = 600) -> str:
    """Load only instructions selected by manifest; reject overflowing budgets."""
    if not 0 <= max_chars <= 20000:
        raise ValueError("Invalid skill instruction budget")
    entries = [f"{skill.name}: {INSTRUCTIONS[skill.name]}"
               for skill in select_skills(goal, strategy=strategy)]
    result = "\n".join(entries)
    if len(result) > max_chars:
        raise ValueError("Selected skill instructions exceed budget")
    return result

# S25-04: one trusted exposure decision supplies both schemas and Skill hints.
# With the current small catalog, let native model tool calling perform semantic
# selection instead of suppressing tools with a brittle keyword classifier.
from typing import Any

@dataclass(frozen=True)
class ToolExposureDecision:
    tool_names: tuple[str, ...]
    skill_names: tuple[str, ...]
    mode: str
    excluded_names: tuple[str, ...] = ()
    version: str = "s25-04-exposure-v1"

def select_tool_exposure(
    goal: str, schemas: list[dict], *, strategy: str = "dynamic",
    prior_observation: dict[str, Any] | None = None,
    required_tools: tuple[str, ...] = (),
) -> ToolExposureDecision:
    if strategy not in ("dynamic", "all"):
        raise ValueError("Unsupported skill strategy")
    # Exposure is NOT authorization; execute_tool remains the trusted boundary.
    registered = {item["function"]["name"] for item in schemas}
    declared = {name for skill in SKILLS for name in skill.tool_names}
    eligible = registered & declared
    # Failing tools remain eligible for model-driven recovery. A model can also
    # decline to call any tool. Observation never registers arbitrary tool names.
    selected = tuple(item["function"]["name"] for item in schemas
                     if item["function"]["name"] in eligible)
    active = tuple(skill.name for skill in SKILLS
                   if any(name in selected for name in skill.tool_names))
    excluded = tuple(sorted(registered - eligible))
    return ToolExposureDecision(selected, active, strategy, excluded)

def exposure_schemas(selection: ToolExposureDecision, schemas: list[dict]) -> list[dict]:
    names = set(selection.tool_names)
    return [schema for schema in schemas if schema["function"]["name"] in names]

def exposure_instructions(selection: ToolExposureDecision, *, max_chars: int = 600) -> str:
    if not 0 <= max_chars <= 20000:
        raise ValueError("Invalid skill instruction budget")
    lines = [f"{name}: {INSTRUCTIONS[name]}" for name in selection.skill_names]
    content = "\n".join(lines)
    if len(content) > max_chars:
        # Preserve the historical hard budget, without silently dropping safety hints.
        raise ValueError("Selected skill instructions exceed budget")
    return content
