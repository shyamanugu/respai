"""`CheckResult` mirrors Orchestration's own definition
(`08-orchestration/src/orchestration/guardrails.py`) but is defined here
independently rather than imported, to keep the dependency direction clean:
this component never imports Orchestration, only the reverse (a usecase or
test wires a concrete guardrail from here into Orchestration's `ModelStep`).
See docs/decisions/0009-guardrails-scope.md.
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
