"""Data shapes for prompt and fragment specs, and the errors the registry
raises when a template references something that doesn't exist or a caller
forgets to supply a required variable.
"""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PromptSpec:
    name: str
    version: int
    description: str
    model_capability: str
    input_variables: list[str]
    template: str
    output_schema: dict | None = None
    source_path: Path | None = field(default=None, repr=False)


@dataclass
class FragmentSpec:
    name: str
    description: str
    template: str
    source_path: Path | None = field(default=None, repr=False)


class PromptNotFoundError(KeyError):
    pass


class FragmentNotFoundError(KeyError):
    pass


class MissingVariableError(ValueError):
    pass


class DuplicatePromptError(ValueError):
    pass
