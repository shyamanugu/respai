"""Fake standing in for a real judge model — every test in this component
runs with no network call and no Azure credentials.
"""


class FakeJudgeProvider:
    def __init__(self, verdict: str = "PASS: looks good") -> None:
        self._verdict = verdict

    def chat(self, deployment, messages, **kwargs):
        return {"content": self._verdict, "input_tokens": 0, "output_tokens": 0}

    def embed(self, deployment, texts):
        raise NotImplementedError("FakeJudgeProvider only supports chat()")
