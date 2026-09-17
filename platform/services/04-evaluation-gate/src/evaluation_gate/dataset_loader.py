"""Loads a golden dataset — one JSON object per line — into `EvalCase`
instances. One file, one dataset; no folder scanning here, the caller
decides which file(s) to load, same division of responsibility as Prompt
Management's loader/registry split.
"""
import json
from pathlib import Path

from .types import EvalCase


def load_dataset(path: Path) -> list[EvalCase]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            cases.append(
                EvalCase(
                    id=raw["id"],
                    input=raw.get("input", {}),
                    evaluator=raw["evaluator"],
                    expected=raw.get("expected"),
                    rubric=raw.get("rubric"),
                    output_schema=raw.get("output_schema"),
                )
            )
    return cases
