"""S2-D run-scoped, validated plan ledger. No side effects or invented completions."""
from dataclasses import dataclass, field

MAX_ITEMS = 8

@dataclass
class PlanLedger:
    steps: list[dict] = field(default_factory=list)
    revision: int = 0
    next_step_id: int = 1
    events: list[dict] = field(default_factory=list)

    def propose(self, steps: list[str]) -> dict:
        if self.steps:
            return {"ok": False, "error": "plan_already_exists"}
        if not self._valid(steps):
            return {"ok": False, "error": "invalid_plan"}
        self.steps = [self._step(i + 1, item) for i, item in enumerate(steps)]
        self.revision = 1
        self.next_step_id = len(self.steps) + 1
        self.events.append({"event": "plan_created", "revision": 1})
        return {"ok": True, "revision": 1, "steps": self.snapshot()}

    def observe(self, tool_name: str, result: dict, arguments: dict | None = None) -> None:
        # Execution evidence does NOT automatically mark a goal completed;
        # the model must name an explicitly verified step.
        if result.get("ok") is True and isinstance(arguments, dict):
            matches = [step for step in self.steps
                       if step["status"] == "pending"
                       and step.get("expected_tool") == tool_name
                       and step.get("expected_arguments") == arguments]
            if len(matches) == 1:
                step = matches[0]
                step["status"] = "completed"
                step["evidence_tool"] = tool_name
                self.events.append({"event": "step_completed", "id": step["id"],
                                    "tool": tool_name, "source": "verified_binding"})
        if not result.get("ok"):
            self.events.append({"event": "tool_failed", "tool": tool_name,
                                "error": result.get("error", "unknown")})

    def apply_verified_recovery(self, step_id: int, original_arguments: dict,
                                replacement_arguments: dict, tool_name: str,
                                observation: dict, strategy: str) -> bool:
        """Version a recovered binding only after successful, verified evidence."""
        from agent.plan_recovery import RecoveryCandidate, validates_recovery
        step = next((s for s in self.steps if s["id"] == step_id), None)
        if (step is None or step["status"] != "pending"
                or step.get("expected_tool") != tool_name
                or step.get("expected_arguments") != original_arguments
                or tool_name != "calculate"
                or not validates_recovery(original_arguments,
                    RecoveryCandidate(tool_name, replacement_arguments, strategy),
                    observation)):
            return False
        step["expected_arguments"] = dict(replacement_arguments)
        self.revision += 1
        self.events.append({"event": "plan_step_recovered",
                            "id": step_id, "revision": self.revision,
                            "strategy": strategy, "source": "verified_observation"})
        self.observe(tool_name, observation, replacement_arguments)
        return step["status"] == "completed"

    def complete(self, step_id: int, evidence_tool: str, observation: dict | None) -> dict:
        step = next((s for s in self.steps if s["id"] == step_id), None)
        if step is None:
            return {"ok": False, "error": "unknown_step"}
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

    def revise(self, expected_revision: int, pending_steps: list[str], reason: str,
               invalidate_step_ids: list[int] | None = None) -> dict:
        if not self.steps:
            return {"ok": False, "error": "plan_not_created", "plan_revision": 0}
        if expected_revision != self.revision:
            return {"ok": False, "error": "stale_plan_revision",
                    "plan_revision": self.revision}
        if not reason.strip() or len(reason) > 180 or not self._valid(pending_steps):
            return {"ok": False, "error": "invalid_revision",
                    "plan_revision": self.revision}
        invalidated = set(invalidate_step_ids or [])
        completed_ids = {s["id"] for s in self.steps if s["status"] == "completed"}
        if len(invalidated) != len(invalidate_step_ids or []) or not invalidated.issubset(completed_ids):
            return {"ok": False, "error": "invalid_invalidation",
                    "plan_revision": self.revision}
        preserved = [dict(step) for step in self.steps
                     if step["status"] == "completed" and step["id"] not in invalidated]
        if len(preserved) + len(pending_steps) > MAX_ITEMS:
            return {"ok": False, "error": "invalid_revision",
                    "plan_revision": self.revision}
        self.steps = preserved + [
            self._step(self.next_step_id + i, goal)
            for i, goal in enumerate(pending_steps)]
        self.next_step_id += len(pending_steps)
        self.revision += 1
        self.events.append({"event": "plan_revised", "revision": self.revision,
                            "preserved": len(preserved), "invalidated": sorted(invalidated),
                            "reason": reason[:180]})
        return {"ok": True, "revision": self.revision, "steps": self.snapshot()}

    @classmethod
    def restore(cls, snapshot: dict) -> "PlanLedger":
        if not isinstance(snapshot, dict):
            raise ValueError("invalid_plan_snapshot")
        steps = snapshot.get("steps")
        revision = snapshot.get("revision")
        if not isinstance(revision, int) or not 1 <= revision <= 100000:
            raise ValueError("invalid_plan_revision")
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_ITEMS:
            raise ValueError("invalid_plan_steps")
        restored = []
        previous_id = 0
        for i, step in enumerate(steps):
            if (not isinstance(step, dict) or type(step.get("id")) is not int
                    or step["id"] <= previous_id):
                raise ValueError("invalid_plan_step")
            previous_id = step["id"]
            if step.get("status") not in ("pending", "completed"):
                raise ValueError("invalid_plan_status")
            raw = {k: step[k] for k in ("goal", "expected_tool", "expected_arguments")
                   if k in step}
            if not cls._valid([raw if "expected_tool" in raw else raw.get("goal")]):
                raise ValueError("invalid_plan_binding")
            if step["status"] == "completed" and not isinstance(
                step.get("evidence_tool"), str):
                raise ValueError("missing_evidence")
            restored.append(dict(step))
        next_step_id = snapshot.get("next_step_id", previous_id + 1)
        if type(next_step_id) is not int or next_step_id <= previous_id:
            raise ValueError("invalid_next_step_id")
        return cls(steps=restored, revision=revision, next_step_id=next_step_id,
                   events=[{"event": "plan_resumed", "revision": revision}])

    def checkpoint(self) -> dict:
        return {"revision": self.revision, "next_step_id": self.next_step_id,
                "steps": self.snapshot()}

    def snapshot(self) -> list[dict]:
        return [dict(s) for s in self.steps]

    @staticmethod
    def _step(step_id: int, item: str | dict) -> dict:
        if isinstance(item, str):
            return {"id": step_id, "goal": item, "status": "pending"}
        return {"id": step_id, "goal": item["goal"], "status": "pending",
                "expected_tool": item["expected_tool"],
                "expected_arguments": item["expected_arguments"]}

    @staticmethod
    def _valid(items: list) -> bool:
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            return False
        for item in items:
            if isinstance(item, str):
                if not 1 <= len(item.strip()) <= 160:
                    return False
            elif isinstance(item, dict):
                if set(item) != {"goal", "expected_tool", "expected_arguments"}:
                    return False
                if not isinstance(item["goal"], str) or not 1 <= len(item["goal"].strip()) <= 160:
                    return False
                if item["expected_tool"] not in ("calculate", "mcp_reference_lookup", "utc_time", "list_tasks", "recall_memory"):
                    return False
                if not isinstance(item["expected_arguments"], dict):
                    return False
            else:
                return False
        return True
