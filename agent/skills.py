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
    }
    matched = [skill for skill in SKILLS
               if any(word in text for word in keywords[skill.name])]
    return tuple(matched) if matched else SKILLS

def exposed_tools(goal: str, schemas: list[dict], *, strategy: str = "dynamic") -> list[dict]:
    names = {name for skill in select_skills(goal, strategy=strategy) for name in skill.tool_names}
    return [schema for schema in schemas if schema["function"]["name"] in names]
