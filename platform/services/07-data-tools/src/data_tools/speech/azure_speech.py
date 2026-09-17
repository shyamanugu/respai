"""Azure AI Speech backend. Reads credentials from AZURE_SPEECH_KEY and
AZURE_SPEECH_REGION (see .env.local — never commit real values to .env).

Not exercised by the automated test suite — like Model Management's
`AzureOpenAIProvider`, this class talks to a live Azure endpoint, so it's
proven manually once real credentials exist, not through a unit test.
`FakeSpeechBackend` (tests/fakes.py) is what the test suite actually runs
against.

The `azure-cognitiveservices-speech` SDK is imported lazily, inside
`__init__`, rather than at module level — a usecase that only needs
retrieval or the generic HTTP connector shouldn't have to install the speech
SDK just to import this package.
"""
import os


class AzureSpeechBackend:
    def __init__(self) -> None:
        import azure.cognitiveservices.speech as speechsdk

        self._speechsdk = speechsdk
        self._speech_config = speechsdk.SpeechConfig(
            subscription=os.environ["AZURE_SPEECH_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
        )

    def transcribe(self, audio_bytes: bytes) -> str:
        speechsdk = self._speechsdk
        audio_stream = speechsdk.audio.PushAudioInputStream()
        audio_stream.write(audio_bytes)
        audio_stream.close()
        audio_config = speechsdk.audio.AudioConfig(stream=audio_stream)
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=self._speech_config, audio_config=audio_config
        )
        result = recognizer.recognize_once()
        return result.text

    def synthesize(self, text: str) -> bytes:
        speechsdk = self._speechsdk
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=self._speech_config, audio_config=None
        )
        result = synthesizer.speak_text_async(text).get()
        return result.audio_data
