from data_tools.speech.tools import SpeechToTextTool, TextToSpeechTool

from .fakes import FakeSpeechBackend


def test_speech_to_text_delegates_to_backend():
    tool = SpeechToTextTool(backend=FakeSpeechBackend())
    assert tool.invoke(b"\x00\x01\x02") == "[transcribed 3 bytes]"


def test_text_to_speech_delegates_to_backend():
    tool = TextToSpeechTool(backend=FakeSpeechBackend())
    assert tool.invoke("hello") == b"[audio for: hello]"
