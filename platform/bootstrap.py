"""Make the AFNI LLMOps platform services importable via PYTHONPATH src-layout.

The platform is consumed by appending each service's ``src`` directory to
``sys.path`` (ADR 0004) rather than installing wheels — this keeps the
reusability acceptance test intact: **zero edits to ``platform/services/**/src``**.
After ``bootstrap()`` runs, callers can ``import observability``,
``import model_management``, ``import guardrails``, etc. by their bare names.

Resolution order for the ``services`` directory:
  1. ``LLMOPS_PLATFORM_ROOT`` env var (may point at ``platform`` or ``platform/services``)
  2. ``services/`` next to this file (``<repo>/platform/services``)

Idempotent and never raises: if the tree is absent, callers degrade to
platform-less, fail-open behaviour (NullTracer / PassthroughGuardrail / etc.).

IMPORTANT: the directory holding this file is named ``platform``, which collides
with the Python standard-library ``platform`` module. Do **not** import this as
``platform.bootstrap``. Load it by file path (see each app's ``llmops`` helper)
or run it as a script.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Every real (importable) platform service. Doc/IaC-only services (01/12/14),
# the empty gateway, and 09-cicd are intentionally excluded — they expose no
# Python API.
REAL_SERVICES = (
    "02-prompt-management",
    "03-model-management",
    "04-evaluation-gate",
    "05-observability",
    "06-guardrails",
    "07-data-tools",
    "08-orchestration",
    "10-serving-hosting",
    "11-feedback",
)

_BOOTSTRAPPED = False


def services_root() -> Path | None:
    """Locate the ``platform/services`` directory, or return ``None``."""
    override = os.environ.get("LLMOPS_PLATFORM_ROOT", "").strip()
    if override:
        root = Path(override).expanduser()
        if (root / "services").is_dir():
            root = root / "services"
        return root if root.is_dir() else None
    candidate = Path(__file__).resolve().parent / "services"
    return candidate if candidate.is_dir() else None


def bootstrap() -> list[str]:
    """Append each real service's ``src`` dir to ``sys.path`` (front).

    Returns the list of directories added (empty if already bootstrapped or the
    platform tree was not found)."""
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return []

    root = services_root()
    added: list[str] = []
    if root is not None:
        for service in REAL_SERVICES:
            src = root / service / "src"
            if src.is_dir():
                path_str = str(src)
                if path_str not in sys.path:
                    sys.path.insert(0, path_str)
                    added.append(path_str)

    _BOOTSTRAPPED = True
    return added


# Run on import / exec so loading this file is sufficient to wire the platform.
bootstrap()

if __name__ == "__main__":
    root = services_root()
    print(f"services_root={root}")
    found = [str(root / s / "src") for s in REAL_SERVICES if root and (root / s / "src").is_dir()]
    print(f"wired {len(found)} service src dir(s):")
    for p in found:
        print(f"  {p}")
