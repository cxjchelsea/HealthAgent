"""S2-C deterministic benchmark: all-tool vs dynamic discovery.

Reports exposure metrics, expected tool coverage and latency. Not model accuracy.
"""
import json
import statistics
import time
from pathlib import Path
from agent.skills import exposed_tools, load_instructions
from agent.tools import TOOL_SCHEMAS

CASES = [
    ("计算 7*6", "calculate"),
    ("现在 UTC 时间是多少", "utc_time"),
    ("创建任务：读书", "create_task"),
    ("列出我的待办任务", "list_tasks"),
    ("记住一个偏好", "save_memory"),
    ("读取我的记忆", "recall_memory"),
    ("请查询 MCP 参考资料", "mcp_reference_lookup"),
    ("Help me calculate 8+9", "calculate"),
    ("Check reference for MCP", "mcp_reference_lookup"),
    ("Do something useful", None),
]


def evaluate():
    variants = {}
    for strategy in ("all", "dynamic"):
        rows = []
        for goal, expected in CASES:
            started = time.perf_counter()
            tools = exposed_tools(goal, TOOL_SCHEMAS, strategy=strategy)
            instructions = load_instructions(goal, strategy=strategy, max_chars=2000)
            names = [tool["function"]["name"] for tool in tools]
            rows.append({
                "case": len(rows) + 1, "expected_visible": expected is None or expected in names,
                "tool_count": len(names),
                "schema_chars": len(json.dumps(tools, ensure_ascii=False)),
                "instruction_chars": len(instructions),
                "selection_ms": round((time.perf_counter() - started) * 1000, 3),
            })
        variants[strategy] = {
            "cases": len(rows),
            "expected_tool_coverage": sum(row["expected_visible"] for row in rows) / len(rows),
            "mean_tool_count": statistics.mean(row["tool_count"] for row in rows),
            "mean_schema_chars": statistics.mean(row["schema_chars"] for row in rows),
            "mean_instruction_chars": statistics.mean(row["instruction_chars"] for row in rows),
            "mean_selection_ms": statistics.mean(row["selection_ms"] for row in rows),
            "results": rows,
        }
    return {
        "suite": "s2c-tool-exposure-comparison-v1",
        "mode": "scripted_selection_not_real_model",
        "variants": variants,
        "limitations": [
            "Selection timing excludes LLM and tool execution latency",
            "Schema chars are not token counts",
            "Expected-tool coverage is not model tool-choice accuracy",
            "Keyword policy may miss implicit intent",
        ],
    }


def main():
    data = evaluate()
    path = Path("evals/results/s2c/selection-v1.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("S2-C dynamic coverage:", data["variants"]["dynamic"]["expected_tool_coverage"])
    print("Evidence:", path)
    if data["variants"]["dynamic"]["expected_tool_coverage"] < 1:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
