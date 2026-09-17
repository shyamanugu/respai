"""Azure AI Content Safety backend — harmful-content moderation via the
`analyze_text` operation (Hate, SelfHarm, Sexual, Violence categories, each
scored 0-7). Reads credentials from AZURE_CONTENT_SAFETY_ENDPOINT and
AZURE_CONTENT_SAFETY_API_KEY (see .env.local — never commit real values to
.env).

Authored to the `azure-ai-contentsafety` SDK shape at time of writing, not
exercised against a live resource — same posture as `AzureAISearchBackend`
and `AzureSpeechBackend` in Data & Tools (07). The SDK is imported lazily,
inside `__init__`, so a usecase running only the free heuristic guardrails
doesn't need this dependency installed.

Prompt Shields (Content Safety's separate jailbreak-detection operation) is
deliberately not wrapped here — its exact SDK call shape wasn't verified
against a live resource at the time this was written. See "Revisit When" in
docs/decisions/0009-guardrails-scope.md.
"""
import os


class AzureContentSafetyBackend:
    def __init__(self) -> None:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.core.credentials import AzureKeyCredential

        self._client = ContentSafetyClient(
            endpoint=os.environ["AZURE_CONTENT_SAFETY_ENDPOINT"],
            credential=AzureKeyCredential(os.environ["AZURE_CONTENT_SAFETY_API_KEY"]),
        )

    def analyze_text(self, text: str) -> dict[str, int]:
        from azure.ai.contentsafety.models import AnalyzeTextOptions

        result = self._client.analyze_text(AnalyzeTextOptions(text=text))
        return {item.category: item.severity for item in result.categories_analysis}
