"""Workflow engine: DAG ordering, retries with backoff, approval gates, resume."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .audit import AuditLog
from .policy import Decision, Mode, Policy
from .store import Store

# A step function receives the results of earlier steps and returns
# (result, confidence). Results must be JSON-serialisable (they are persisted).
StepFn = Callable[[dict], tuple[object, float]]


class CycleError(ValueError):
    pass


@dataclass
class Step:
    name: str
    fn: StepFn
    deps: tuple[str, ...] = ()
    mode: Mode = Mode.AUTO
    retries: int = 2
    cost: float = 0.0


@dataclass
class RunResult:
    run_id: str
    status: str  # completed | waiting | failed
    results: dict = field(default_factory=dict)
    waiting_on: list[str] = field(default_factory=list)
    error: str | None = None


class Workflow:
    def __init__(self, name: str, store: Store, audit: AuditLog,
                 policy: Policy | None = None, sleep: Callable[[float], None] = time.sleep):
        self.name, self.store, self.audit = name, store, audit
        self.policy = policy or Policy()
        self.sleep = sleep
        self.steps: dict[str, Step] = {}

    def add(self, step: Step) -> "Workflow":
        if step.name in self.steps:
            raise ValueError(f"duplicate step {step.name!r}")
        self.steps[step.name] = step
        return self

    def order(self) -> list[str]:
        """Kahn's algorithm; raises CycleError or on unknown dependencies."""
        for s in self.steps.values():
            for d in s.deps:
                if d not in self.steps:
                    raise ValueError(f"step {s.name!r} depends on unknown step {d!r}")
        indeg = {n: len(s.deps) for n, s in self.steps.items()}
        ready = sorted(n for n, k in indeg.items() if k == 0)
        out: list[str] = []
        while ready:
            n = ready.pop(0)
            out.append(n)
            for m in sorted(self.steps):
                if n in self.steps[m].deps:
                    indeg[m] -= 1
                    if indeg[m] == 0:
                        ready.append(m)
        if len(out) != len(self.steps):
            raise CycleError("workflow contains a cycle")
        return out

    # -- execution ---------------------------------------------------------

    def start(self, run_id: str | None = None) -> RunResult:
        run_id = run_id or uuid.uuid4().hex[:8]
        self.audit.record("run_started", run=run_id, workflow=self.name)
        return self._execute(run_id, {})

    def resume(self, run_id: str) -> RunResult:
        loaded = self.store.load_run(run_id)
        if loaded is None:
            raise LookupError(f"unknown run {run_id}")
        return self._execute(run_id, loaded[2].get("results", {}))

    def approve(self, run_id: str, step: str, approver: str, approved: bool = True) -> None:
        self.store.decide(run_id, step, approved, approver)
        self.audit.record("approval", run=run_id, step=step, approver=approver, approved=approved)

    def _execute(self, run_id: str, results: dict) -> RunResult:
        waiting: list[str] = []
        for name in self.order():
            step = self.steps[name]
            if name in results:
                held = results[name]
                if isinstance(held, dict) and held.get("held"):
                    verdict = self.store.approval_status(run_id, name)
                    if verdict == "approved":
                        results[name] = held["value"]
                    elif verdict == "rejected":
                        return self._finish(run_id, "failed", results,
                                            error=f"{name} output rejected by human")
                    else:
                        waiting.append(name)
                continue
            if any(d not in results or self._is_held(results[d]) for d in step.deps):
                continue  # an upstream step is still waiting
            gate = self.store.approval_status(run_id, name)
            if gate == "rejected":
                return self._finish(run_id, "failed", results, error=f"{name} rejected by human")
            decision = Decision(Mode.AUTO, "previously approved") if gate == "approved" else \
                self._gate(step, results)
            if decision.mode is Mode.ESCALATE:
                self.store.request_approval(run_id, name, "ESCALATED: " + decision.reason)
                self.audit.record("escalated", run=run_id, step=name, reason=decision.reason)
                waiting.append(name)
                continue
            if decision.mode is Mode.APPROVE and gate != "approved":
                self.store.request_approval(run_id, name, decision.reason)
                self.audit.record("approval_requested", run=run_id, step=name, reason=decision.reason)
                waiting.append(name)
                continue
            outcome = self._run_step(run_id, step, results)
            if outcome is None:
                return self._finish(run_id, "failed", results, error=f"{name} failed after retries")
            results[name] = outcome
            if self._is_held(outcome):
                waiting.append(name)
        if waiting:
            return self._finish(run_id, "waiting", results, waiting=waiting)
        return self._finish(run_id, "completed", results)

    @staticmethod
    def _is_held(value: object) -> bool:
        return isinstance(value, dict) and bool(value.get("held"))

    def _gate(self, step: Step, results: dict) -> Decision:
        """Dry-run confidence check is the step's own responsibility; here we only
        apply the declared mode, cost ceiling and any prior low-confidence signal."""
        return self.policy.decide(step.mode, confidence=1.0, cost=step.cost)

    def _run_step(self, run_id: str, step: Step, results: dict):
        for attempt in range(step.retries + 1):
            try:
                value, confidence = step.fn(dict(results))
                decision = self.policy.decide(Mode.AUTO, confidence)
                if decision.mode is not Mode.AUTO and step.mode is Mode.AUTO:
                    # Output was produced but confidence is too low to ship unattended:
                    # hold the result for a human rather than discarding the work.
                    self.store.request_approval(run_id, step.name, "post-run: " + decision.reason)
                    self.audit.record("held_for_review", run=run_id, step=step.name,
                                      confidence=confidence)
                    return {"value": value, "held": True, "confidence": confidence}
                self.audit.record("step_ok", run=run_id, step=step.name, attempt=attempt,
                                  confidence=confidence)
                return value
            except Exception as exc:  # noqa: BLE001 - recorded, then retried
                self.audit.record("step_error", run=run_id, step=step.name,
                                  attempt=attempt, error=repr(exc))
                if attempt < step.retries:
                    self.sleep(min(2 ** attempt * 0.1, 2.0))
        return None

    def _finish(self, run_id: str, status: str, results: dict, waiting=None, error=None) -> RunResult:
        self.store.save_run(run_id, self.name, status, {"results": results})
        self.audit.record("run_" + status, run=run_id, waiting=waiting or [], error=error)
        return RunResult(run_id, status, results, waiting or [], error)
