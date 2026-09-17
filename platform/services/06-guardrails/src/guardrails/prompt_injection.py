"""Heuristic prompt-injection / jailbreak detection on input text — pattern
matching against known injection phrasing. A cheap, always-available first
line of defense, not a substitute for a real classifier — see
`AzureContentSafetyGuardrail`'s docstring and
docs/decisions/0009-guardrails-scope.md for why Content Safety's Prompt
Shields (a stronger, Azure-backed alternative) isn't wrapped here yet.
Input-only: injection is an attack on the prompt, not something that shows
up in a model's output.
"""
import re
from dataclasses import dataclass, field

from .types import CheckResult

_DEFAULT_PATTERNS = [
    r"ignore (all|the|any) (previous|prior|above) instructions",
    r"disregard (the|your) (system|previous) prompt",
    r"you are now (in )?(dan|developer mode)",
    r"reveal (your|the) (system prompt|instructions)",
    r"act as if you have no (restrictions|rules|guidelines)",
    r"pretend (you are|to be) an? (unfiltered|unrestricted|jailbroken)",
]


@dataclass
class PromptInjectionGuardrail:
    patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_PATTERNS))

    def check_input(self, text: str) -> CheckResult:
        lowered = text.lower()
        for pattern in self.patterns:
            if re.search(pattern, lowered):
                return CheckResult(
                    allowed=False, reason=f"Possible prompt injection matched pattern: '{pattern}'"
                )
        return CheckResult(allowed=True)

    def check_output(self, text: str) -> CheckResult:
        return CheckResult(allowed=True)
