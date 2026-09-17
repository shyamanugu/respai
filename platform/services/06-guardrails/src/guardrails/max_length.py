"""Caps input and/or output length — the simplest guardrail here, a backstop
against runaway prompts or responses rather than a content check. `None`
(the default) means no cap on that side.
"""
from dataclasses import dataclass

from .types import CheckResult


@dataclass
class MaxLengthGuardrail:
    max_input_chars: int | None = None
    max_output_chars: int | None = None

    def check_input(self, text: str) -> CheckResult:
        if self.max_input_chars is not None and len(text) > self.max_input_chars:
            return CheckResult(allowed=False, reason=f"Input exceeds {self.max_input_chars} characters")
        return CheckResult(allowed=True)

    def check_output(self, text: str) -> CheckResult:
        if self.max_output_chars is not None and len(text) > self.max_output_chars:
            return CheckResult(allowed=False, reason=f"Output exceeds {self.max_output_chars} characters")
        return CheckResult(allowed=True)
