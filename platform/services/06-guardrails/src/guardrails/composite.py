"""Combines multiple guardrail checks into one object satisfying the same
check_input/check_output shape Orchestration's `ModelStep` already expects
via its `guardrail` parameter — a drop-in replacement for
`PassthroughGuardrail`, no change needed in Orchestration itself.

Every check runs, even after one blocks — reasons from every check are
joined into the result, not just the first blocking one, so the eventual
Observability component (05) has the full picture to log, not just whichever
check happened to run first.
"""
from dataclasses import dataclass, field

from .types import CheckResult, GuardrailCheck


@dataclass
class CompositeGuardrail:
    checks: list[GuardrailCheck] = field(default_factory=list)

    def check_input(self, text: str) -> CheckResult:
        return self._run(text, "check_input")

    def check_output(self, text: str) -> CheckResult:
        return self._run(text, "check_output")

    def _run(self, text: str, method_name: str) -> CheckResult:
        reasons = []
        blocked = False
        for check in self.checks:
            result = getattr(check, method_name)(text)
            if result.reason:
                reasons.append(result.reason)
            if not result.allowed:
                blocked = True
        return CheckResult(allowed=not blocked, reason="; ".join(reasons))
