"""Shared data shapes for retrieval results."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    content: str
    source: str
    score: float
