"""S2-D run-scoped, validated plan ledger. No side effects or invented completions."""
from dataclasses import dataclass, field

MAX_ITEMS = 8

@dataclass
class PlanLedger:
    steps: list[dict] = field(default_factory=list)
    revision: int = 0
    events: list[dict] = field(default_factory=list)

    def propose(self, steps: list[str]) -> dict:
        if self.steps:
            return {"ok": False, "error": "plan_already_exists"}
        if not self._valid(steps):
            return {"ok": False, "error": "invalid_plan"}
        self.steps = [{"id": i + 1, "goal": title, "status": "pending"}
                      for i, title in enumerate(steps)]
        self.revision = 1
        self.events.append({"event": "plan_created", "revision": 1})
        return {"ok": True, "revision": 1, "steps": self.snapshot()}

    def observe(self, tool_name: str, result: dict) -> None:
        # Execution evidence does NOT automatically mark a goal completed;
        # the model must name an explicitly verified step.
        if not result.get("ok"):
            self.events.append({"event": "tool_failed", "tool": tool_name,
                                "error": result.get("error", "unknown")})

    def complete(self, step_id: int, evidence_tool: str, observation: dict | None) -> dict:
        if not self.steps or not 1 <= step_id <= len(self.steps):
            return {"ok": False, "error": "unknown_step"}
        step = self.steps[step_id - 1]
        if step["status"] != "pending":
            return {"ok": False, "error": "step_not_pending"}
        if not observation or not observation.get("ok"):
            return {"ok": False, "error": "missing_success_evidence"}
        if observation.get("name") != evidence_tool:
            return {"ok": False, "error": "evidence_tool_mismatch"}
        step["status"] = "completed"
        step["evidence_tool"] = evidence_tool
        self.events.append({"event": "step_completed", "id": step_id,
                            "tool": evidence_tool})
        return {"ok": True, "steps": self.snapshot()}

    def revise(self, expected_revision: int, pending_steps: list[str], reason: str) -> dict:
        if not self.steps or expected_revision != self.revision:
            return {"ok": False, "error": "stale_plan_revision"}
        if not reason.strip() or len(reason) > 180 or not self._valid(pending_steps):
            return {"ok": False, "error": "invalid_revision"}
        preserved = [dict(step) for step in self.steps if step["status"] == "completed"]
        self.steps = preserved + [
            {"id": len(preserved) + i + 1, "goal": goal, "status": "pending"}
            for i, goal in enumerate(pending_steps)]
        self.revision += 1
        self.events.append({"event": "plan_revised", "revision": self.revision,
                            "preserved": len(preserved), "reason": reason[:180]})
        return {"ok": True, "revision": self.revision, "steps": self.snapshot()}

    def snapshot(self) -> list[dict]:
        return [dict(s) for s in self.steps]

    @staticmethod
    def _valid(items: list[str]) -> bool:
        return isinstance(items, list) and 1 <= len(items) <= MAX_ITEMS and all(
            isinstance(x, str) and 1 <= len(x.strip()) <= 160 for x in items)
