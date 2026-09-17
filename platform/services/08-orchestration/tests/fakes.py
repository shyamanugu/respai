"""A fake model provider so pipeline tests run with no live Azure call and no
deployed resource. Returns canned responses keyed by a substring match on the
prompt, which is enough to prove state threads correctly between steps.
"""
from collections.abc import Sequence


class FakeModelProvider:
    def __init__(self, responses: dict):
        """`responses` maps a substring to the canned reply returned when
        that substring appears in the prompt sent to `chat`."""
        self._responses = responses

    def chat(self, deployment: str, messages: Sequence[dict], **kwargs) -> dict:
        prompt = messages[-1]["content"]
        for substring, reply in self._responses.items():
            if substring in prompt:
                return {"content": reply, "input_tokens": 10, "output_tokens": 10}
        return {"content": "no canned response matched", "input_tokens": 10, "output_tokens": 10}

    def embed(self, deployment: str, texts: Sequence[str]) -> list:
        return [[0.0] for _ in texts]
