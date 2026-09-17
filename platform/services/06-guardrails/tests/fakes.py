"""Fake standing in for the real Azure Content Safety backend — every test
in this component runs with no network call and no Azure credentials.
"""


class FakeContentSafetyBackend:
    def __init__(self, severities: dict | None = None) -> None:
        self._severities = severities or {}

    def analyze_text(self, text: str) -> dict:
        return dict(self._severities)
