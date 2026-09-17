"""Azure OpenAI provider adapter. Reads credentials from AZURE_OPENAI_ENDPOINT and
AZURE_OPENAI_API_KEY (see .env.local — never commit real values to .env).

Managed-identity auth (keyless) is deferred until the RBAC role assignment in
docs/checklist/BUILD-CHECKLIST.md (Phase 0, item 3) is granted; this adapter's
public interface will not need to change when that happens, only how it
authenticates internally.
"""
import os
from collections.abc import Sequence

from openai import AzureOpenAI

from .base import ChatMessage, ChatResponse


class AzureOpenAIProvider:
    def __init__(self) -> None:
        self._client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version="2024-10-21",
        )

    def chat(
        self, deployment: str, messages: Sequence[ChatMessage], **kwargs
    ) -> ChatResponse:
        response = self._client.chat.completions.create(
            model=deployment, messages=list(messages), **kwargs
        )
        choice = response.choices[0].message
        return ChatResponse(
            content=choice.content or "",
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
        )

    def embed(self, deployment: str, texts: Sequence[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=deployment, input=list(texts))
        return [item.embedding for item in response.data]
