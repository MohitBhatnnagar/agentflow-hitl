"""Autonomy policy: decide whether a step may run unattended.

Three modes, in increasing order of human involvement:
  AUTO      the agent runs the step and nobody is asked.
  APPROVE   the step pauses until a named human approves or rejects it.
  ESCALATE  the step is never run by the agent; it is handed to an owner.

A step's declared mode is a ceiling on autonomy, never a floor: low model
confidence can only move a step toward more human involvement.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Mode(IntEnum):
    AUTO = 0
    APPROVE = 1
    ESCALATE = 2


@dataclass(frozen=True)
class Decision:
    mode: Mode
    reason: str


@dataclass(frozen=True)
class Policy:
    auto_min_confidence: float = 0.80      # below this, AUTO becomes APPROVE
    escalate_below_confidence: float = 0.40  # below this, anything becomes ESCALATE
    max_auto_cost: float = 50.0            # spend ceiling for unattended steps

    def decide(self, declared: Mode, confidence: float, cost: float = 0.0) -> Decision:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        mode, reason = declared, f"declared {declared.name}"
        if confidence < self.escalate_below_confidence:
            return Decision(Mode.ESCALATE, f"confidence {confidence:.2f} below escalation floor")
        if mode is Mode.AUTO and confidence < self.auto_min_confidence:
            mode, reason = Mode.APPROVE, f"confidence {confidence:.2f} below auto threshold"
        if mode is Mode.AUTO and cost > self.max_auto_cost:
            mode, reason = Mode.APPROVE, f"cost {cost:.2f} above unattended ceiling"
        return Decision(mode, reason)
