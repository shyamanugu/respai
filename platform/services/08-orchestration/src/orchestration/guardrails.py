"""Guardrail check interface. `PassthroughGuardrail` is a no-op default so every
step can route through a check point now — component 06 (Guardrails) replaces
this with real input/output checks (PII, injection, safety) without changing
how steps call it.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class CheckResult:
    allowed: bool
    reason: str = ""


class GuardrailCheck(Protocol):
    def check_input(self, text: str) -> CheckResult:
        ...

    def check_output(self, text: str) -> CheckResult:
        ...


class PassthroughGuardrail:
    """No-op default. Every step passes through this until component 06 exists."""

    def check_input(self, text: str) -> CheckResult:
        return CheckResult(allowed=True)

    def check_output(self, text: str) -> CheckResult:
        return CheckResult(allowed=True)


class GuardrailBlockedError(RuntimeError):
    """Raised when a guardrail check disallows a step's input or output."""
