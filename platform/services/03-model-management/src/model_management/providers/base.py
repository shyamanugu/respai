"""Provider interface. Every model provider (Azure OpenAI today, others later)
implements this so the resolver and orchestration code never depend on a
specific SDK directly."""
from collections.abc import Sequence
from typing import Protocol, TypedDict


class ChatMessage(TypedDict):
    role: str
    content: str


class ChatResponse(TypedDict):
    content: str
    input_tokens: int
    output_tokens: int


class ModelProvider(Protocol):
    def chat(
        self, deployment: str, messages: Sequence[ChatMessage], **kwargs
    ) -> ChatResponse:
        ...

    def embed(self, deployment: str, texts: Sequence[str]) -> list[list[float]]:
        ...
