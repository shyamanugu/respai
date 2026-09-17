"""Wraps Azure AI Content Safety's harm-category moderation as a
GuardrailCheck. Not exercised by the automated test suite — the backend
talks to a live Azure endpoint; `FakeContentSafetyBackend`
(tests/fakes.py) is what tests actually run against, injected via
`backend_factory` the same way `ModelStep` injects `provider_factory`.
"""
from collections.abc import Callable
from dataclasses import dataclass, field

from .types import CheckResult


def _default_backend_factory():
    from .azure_content_safety_backend import AzureContentSafetyBackend

    return AzureContentSafetyBackend()


@dataclass
class AzureContentSafetyGuardrail:
    severity_threshold: int = 4
    backend_factory: Callable[[], object] = field(default=_default_backend_factory, repr=False)

    def _check(self, text: str) -> CheckResult:
        backend = self.backend_factory()
        severities = backend.analyze_text(text)
        flagged = {category: score for category, score in severities.items() if score >= self.severity_threshold}
        if flagged:
            detail = ", ".join(f"{category}={score}" for category, score in flagged.items())
            return CheckResult(allowed=False, reason=f"Content Safety flagged: {detail}")
        return CheckResult(allowed=True)

    def check_input(self, text: str) -> CheckResult:
        return self._check(text)

    def check_output(self, text: str) -> CheckResult:
        return self._check(text)
