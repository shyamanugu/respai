"""Enables ``python -m chatbot.ingestion`` (from the repo root) or
``python -m ingestion`` (from inside chatbot/) to run the blob → SQLite CLI."""

# --- Path bootstrap: make the absolute ``chatbot.*`` imports resolve even when
# this package is invoked from inside the chatbot/ folder. See main.py for the
# rationale (keeps the ``chatbot.*`` logger namespace intact).
import os as _os
import sys as _sys

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)

from chatbot.ingestion.jobs import _cli

if __name__ == "__main__":
    _cli()
