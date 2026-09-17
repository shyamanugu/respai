"""Shared types for model resolution."""
from dataclasses import dataclass
from enum import Enum


class ModelKind(str, Enum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    REALTIME = "realtime"


@dataclass(frozen=True)
class ModelHandle:
    """A fully resolved model reference — everything a caller needs to invoke it,
    without needing to know how the alias was configured."""

    alias: str
    provider: str
    deployment: str
    kind: ModelKind
