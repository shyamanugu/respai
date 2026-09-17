"""Feedback capture + promotion for APIX. Thin wrapper over the platform's
feedback store; writes to a JSONL file (``APIX_FEEDBACK_PATH``) so it works with
zero Azure, and can promote human corrections into an eval golden dataset that
the evaluation gate loads directly.

CLI:
    python -m ai_pipeline.llmops.feedback_gate record --session <id> --step analysis \
        --rating down --comment "wrong sentiment" --corrected '{"sentiment":"positive"}'
    python -m ai_pipeline.llmops.feedback_gate promote --out eval/dataset/promoted_golden.jsonl
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .context import current_run_id


def _feedback_path() -> Path:
    return Path(os.environ.get("APIX_FEEDBACK_PATH", "data/feedback/feedback.jsonl"))


def _store():
    try:
        from feedback.store import JsonlFileFeedbackStore

        return JsonlFileFeedbackStore(_feedback_path())
    except Exception:
        return None


def record_feedback(
    *,
    session_id: str | None = None,
    step_name: str = "",
    rating: str = "",
    comment: str = "",
    corrected_output: str | None = None,
    original_input: dict | None = None,
    rater_role: str = "reviewer",
) -> bool:
    """Record one feedback event. Returns True if persisted, False (fail-open)
    if the platform/store is unavailable."""
    store = _store()
    if store is None:
        return False
    try:
        from feedback.types import FeedbackEvent

        event = FeedbackEvent(
            session_id=session_id or current_run_id(),
            step_name=step_name or "llm",
            rating=rating,
            original_input=original_input or {},
            corrected_output=corrected_output,
            rater_role=rater_role,
            comment=comment,
        )
        store.record(event)
        return True
    except Exception:
        return False


def _load_all_events() -> list:
    """Load every FeedbackEvent from the JSONL file (the store only lists by
    session). Returns [] fail-open."""
    path = _feedback_path()
    if not path.exists():
        return []
    try:
        from feedback.types import FeedbackEvent

        events = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(FeedbackEvent(**json.loads(line)))
        return events
    except Exception:
        return []


def promote(out_path: str) -> int:
    """Promote recorded corrections into an eval golden dataset. Returns count."""
    try:
        from feedback.promotion import promote_to_golden_dataset

        return promote_to_golden_dataset(_load_all_events(), Path(out_path))
    except Exception:
        return 0


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="APIX feedback gate")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--session", default=None)
    r.add_argument("--step", default="llm")
    r.add_argument("--rating", default="")
    r.add_argument("--comment", default="")
    r.add_argument("--corrected", default=None, help="corrected output (becomes golden expected)")
    pr = sub.add_parser("promote")
    pr.add_argument("--out", default="eval/dataset/promoted_golden.jsonl")
    args = p.parse_args()

    if args.cmd == "record":
        ok = record_feedback(
            session_id=args.session, step_name=args.step, rating=args.rating,
            comment=args.comment, corrected_output=args.corrected,
        )
        print("recorded" if ok else "not recorded (platform/store unavailable)")
    elif args.cmd == "promote":
        n = promote(args.out)
        print(f"promoted {n} case(s) -> {args.out}")
