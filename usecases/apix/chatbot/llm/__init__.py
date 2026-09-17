"""llm — Azure OpenAI client, prompt loading, NL narration, and the KPI dictionary.

The public LLM helpers live in :mod:`chatbot.llm.client`; they are re-exported
here so callers keep using ``from chatbot.llm import generate_response`` etc.
"""

from chatbot.llm.client import *  # noqa: F401,F403
