"""Shared Pydantic models used by ALL programs.

These are program-agnostic — denoise output and reflection output.
Program-specific evaluation schemas live in their own folder
(e.g. ``telesales/schema.py``).
"""

from pydantic import BaseModel, Field
from typing import List, Literal


# ── Denoise output ───────────────────────────────────────────────────────────

class Utterance(BaseModel):
    speaker: Literal["Agent", "Customer"]
    text: str


class Transcript(BaseModel):
    transcript: List[Utterance]


# ── Reflection / Summary output ─────────────────────────────────────────────

class TranscriptReference(BaseModel):
    date_utc: str
    reference_id: int
    segment_ids: List[int] = Field(min_length=1)
    explanation: str


class WeeklyCoachingTip(BaseModel):
    tip: str
    priority: Literal["Very High", "High", "Medium"]
    examples: List[TranscriptReference]
    expected_impact: str


class ReflectionResponse(BaseModel):
    overall_summary: str
    coaching_tips: List[WeeklyCoachingTip]
    key_improvements: List[str]
