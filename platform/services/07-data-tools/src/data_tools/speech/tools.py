"""STT/TTS pipeline tools — thin `Tool`-protocol wrappers around a speech
backend, so a pipeline step can call transcription or synthesis the same way
it calls any other tool.
"""
from dataclasses import dataclass, field

from .azure_speech import AzureSpeechBackend
from .base import SpeechToTextBackend, TextToSpeechBackend


@dataclass
class SpeechToTextTool:
    name: str = "transcribe_audio"
    description: str = "Converts spoken audio into text using the STT/TTS pipeline (not the Realtime API)."
    backend: SpeechToTextBackend = field(default_factory=AzureSpeechBackend)

    def invoke(self, audio_bytes: bytes) -> str:
        return self.backend.transcribe(audio_bytes)


@dataclass
class TextToSpeechTool:
    name: str = "synthesize_speech"
    description: str = "Converts text into spoken audio using the STT/TTS pipeline (not the Realtime API)."
    backend: TextToSpeechBackend = field(default_factory=AzureSpeechBackend)

    def invoke(self, text: str) -> bytes:
        return self.backend.synthesize(text)
