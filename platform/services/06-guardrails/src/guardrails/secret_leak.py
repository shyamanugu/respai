"""Detects secret-shaped strings (cloud credentials, private key headers) in
OUTPUT text — a model should never emit these regardless of what the prompt
asked for. Output-only: a user pasting a key as input (e.g. asking for help
debugging one) is a legitimate, if risky, input case, not something this
check second-guesses.

`generic_api_key` is disabled by default — a bare 32+ character alphanumeric
match is noisy against normal text (long hashes, IDs, encoded values) and
would produce too many false blocks; enable it per usecase in
config/guardrails.yaml only if that usecase's traffic makes it a reasonable
signal.
"""
import re
from dataclasses import dataclass, field

from .types import CheckResult

_PATTERNS = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "azure_connection_string": re.compile(r"AccountKey=[A-Za-z0-9+/=]{20,}"),
    "private_key_header": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "generic_api_key": re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
}

_DEFAULT_ENABLED = {
    "aws_access_key": True,
    "azure_connection_string": True,
    "private_key_header": True,
    "generic_api_key": False,
}


@dataclass
class SecretLeakGuardrail:
    enabled_categories: dict[str, bool] = field(default_factory=lambda: dict(_DEFAULT_ENABLED))

    def check_input(self, text: str) -> CheckResult:
        return CheckResult(allowed=True)

    def check_output(self, text: str) -> CheckResult:
        found = [
            category
            for category, pattern in _PATTERNS.items()
            if self.enabled_categories.get(category, _DEFAULT_ENABLED.get(category, False))
            and pattern.search(text)
        ]
        if found:
            return CheckResult(allowed=False, reason=f"Possible secret leak detected: {', '.join(found)}")
        return CheckResult(allowed=True)
