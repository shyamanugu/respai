"""Parses a single prompt or fragment YAML file into its dataclass. One file
on disk always maps to exactly one spec — no multi-document YAML, no folder
scanning here (that's the registry's job).
"""
from pathlib import Path

import yaml

from .types import FragmentSpec, PromptSpec


def load_prompt_file(path: Path) -> PromptSpec:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return PromptSpec(
        name=raw["name"],
        version=raw.get("version", 1),
        description=raw.get("description", ""),
        model_capability=raw["model_capability"],
        input_variables=raw.get("input_variables", []),
        template=raw["template"],
        output_schema=raw.get("output_schema"),
        source_path=path,
    )


def load_fragment_file(path: Path) -> FragmentSpec:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return FragmentSpec(
        name=raw["name"],
        description=raw.get("description", ""),
        template=raw["template"],
        source_path=path,
    )
