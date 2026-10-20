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
        "reference": ("参考", "资料", "词条", "术语", "reference", "catalog", "mcp"),
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
    "planning": "For complex multi-step work use propose_plan, revise pending steps after failed observations, and define verifiable steps as {goal,expected_tool,expected_arguments}; exact successful observations auto-complete uniquely bound steps. Unbound steps require explicit complete_plan_step.",
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
