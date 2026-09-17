"""Speech backend interfaces for the STT/TTS pipeline. This is deliberately
separate from Model Management's `voice` alias (kind `realtime`): that alias
is a single Realtime API model, this is a pipeline composed of two ordinary
steps (transcribe, then synthesize) around whatever chat alias a usecase
already uses. See docs/decisions/0003-model-management-scope.md for the
boundary and docs/decisions/0007-data-tools-scope.md for why both
architectures are offered rather than forcing one.
"""
from typing import Protocol


class SpeechToTextBackend(Protocol):
    def transcribe(self, audio_bytes: bytes) -> str:
        ...


class TextToSpeechBackend(Protocol):
    def synthesize(self, text: str) -> bytes:
        ...
