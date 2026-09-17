"""Keyword/phrase blocklist — case-insensitive substring match against a
configurable list. Applies to both input and output. This component ships
no default terms of its own — what's "banned" (profanity, competitor names,
specific phrases) is a usecase/business decision, supplied via
config/guardrails.yaml.
"""
from dataclasses import dataclass, field

from .types import CheckResult


@dataclass
class BlocklistGuardrail:
    terms: list[str] = field(default_factory=list)

    def _check(self, text: str) -> CheckResult:
        lowered = text.lower()
        hits = [term for term in self.terms if term.lower() in lowered]
        if hits:
            return CheckResult(allowed=False, reason=f"Blocked term(s) found: {', '.join(hits)}")
        return CheckResult(allowed=True)

    def check_input(self, text: str) -> CheckResult:
        return self._check(text)

    def check_output(self, text: str) -> CheckResult:
        return self._check(text)
