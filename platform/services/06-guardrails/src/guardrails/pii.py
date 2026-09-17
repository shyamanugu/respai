"""Regex-based PII detection — no Azure dependency, $0, always available.
Detects common PII shapes (email, phone, SSN-like, credit-card-like) in
input and/or output text.

Each category can be set to `block`, `flag`, or `off` per side. The
directional defaults differ: PII in *input* is often legitimate — a
customer providing their own phone number or account details — so it
defaults to `flag` (allowed through, but recorded in the result's reason).
PII in *output* the model shouldn't be inventing or repeating (another
customer's record, a fabricated SSN) defaults to `block`.
"""
import re
from dataclasses import dataclass, field

from .types import CheckResult

_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
}


@dataclass
class PIIGuardrail:
    input_mode: dict[str, str] = field(default_factory=dict)
    output_mode: dict[str, str] = field(default_factory=dict)
    default_input_mode: str = "flag"
    default_output_mode: str = "block"

    def _check(self, text: str, mode_map: dict[str, str], default_mode: str) -> CheckResult:
        found = []
        blocking = []
        for category, pattern in _PATTERNS.items():
            mode = mode_map.get(category, default_mode)
            if mode == "off":
                continue
            if pattern.search(text):
                found.append(category)
                if mode == "block":
                    blocking.append(category)

        if blocking:
            return CheckResult(allowed=False, reason=f"PII detected (blocked): {', '.join(blocking)}")
        if found:
            return CheckResult(
                allowed=True, reason=f"PII detected (flagged, not blocked): {', '.join(found)}"
            )
        return CheckResult(allowed=True)

    def check_input(self, text: str) -> CheckResult:
        return self._check(text, self.input_mode, self.default_input_mode)

    def check_output(self, text: str) -> CheckResult:
        return self._check(text, self.output_mode, self.default_output_mode)
