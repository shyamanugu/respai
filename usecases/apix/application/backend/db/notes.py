"""
backend/db/notes.py — Coaching notes storage (Azure Blob)
==========================================================
Stores coaching notes as JSON files in the Azure Blob storage under the
'notes/' prefix in the main container. Each employee gets a single blob:
notes/{emp_id}.json containing a list of note dictionaries.

Passing ``namespace`` (e.g. "metrics") isolates a note store under a sub-prefix
(notes/{namespace}/{emp_id}.json) so different pages keep separate, non-mixing
note stores. The default (empty namespace) is the Individual Report store.

Each note entry:
{
    "id": "<uuid>",
    "author": "<user_id>",
    "note": "<text>",
    "created_at": "<iso timestamp>",
    "updated_at": "<iso timestamp>",
    "hidden": false
}
"""

import json
import os
import uuid
from datetime import datetime
from typing import Dict, List

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv

from backend.config.logging import get_logger

logger = get_logger(__name__)
load_dotenv()

# ---------------------------------------------------------------------------
# Blob connection — uses the SAME container as main data, path prefix "notes/"
# ---------------------------------------------------------------------------
_CONN_STR = os.getenv("AZURE_BLOB_CONNECTION_STRING")
_CONTAINER_NAME = os.getenv("AZURE_BLOB_CONTAINER", "")
_NOTES_PREFIX = "notes"

_blob_service: BlobServiceClient | None = None
_container_client = None

if _CONN_STR and _CONTAINER_NAME:
    _blob_service = BlobServiceClient.from_connection_string(_CONN_STR)
    _container_client = _blob_service.get_container_client(_CONTAINER_NAME)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _blob_path(emp_id: str, namespace: str = "") -> str:
    """Return the blob path for an employee's notes file.

    When ``namespace`` is set the notes are isolated under a sub-prefix
    (``notes/{namespace}/{emp_id}.json``) so different pages/contexts keep
    separate note stores and never mix. Default (empty) preserves the original
    ``notes/{emp_id}.json`` path used by the Individual Report.
    """
    if namespace:
        return f"{_NOTES_PREFIX}/{namespace}/{emp_id}.json"
    return f"{_NOTES_PREFIX}/{emp_id}.json"


def _load_notes_blob(emp_id: str, namespace: str = "") -> List[Dict]:
    """Load the full list of notes for an employee from blob."""
    if not _container_client:
        logger.warning("Blob container not configured — cannot load notes")
        return []
    path = _blob_path(emp_id, namespace)
    try:
        bc = _container_client.get_blob_client(path)
        raw = bc.download_blob().readall().decode("utf-8-sig")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        # If legacy format is a dict with "notes" key, unwrap
        if isinstance(data, dict) and "notes" in data:
            return data["notes"] if isinstance(data["notes"], list) else []
        return []
    except ResourceNotFoundError:
        logger.debug("No notes blob found at %s — starting fresh", path)
        return []
    except Exception as exc:
        logger.error("Failed to load notes for emp=%s: %s", emp_id, exc)
        return []


def _save_notes_blob(emp_id: str, notes: List[Dict], namespace: str = "") -> None:
    """Persist the full list of notes for an employee to blob."""
    if not _container_client:
        logger.warning("Blob container not configured — cannot save notes")
        return
    path = _blob_path(emp_id, namespace)
    try:
        bc = _container_client.get_blob_client(path)
        bc.upload_blob(
            json.dumps(notes, indent=2, ensure_ascii=False),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json"),
        )
        logger.debug("Notes saved at %s (%d entries)", path, len(notes))
    except Exception as exc:
        logger.error("Failed to save notes for emp=%s: %s", emp_id, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_note(emp_id: str, author: str, note: str, week: str = "",
             author_name: str = "", author_role: str = "", namespace: str = "") -> str:
    """Insert a coaching note. Returns the new note id.

    ``namespace`` isolates the note store (e.g. "metrics" for the Individual
    Performance Metrics page) so it never mixes with the default report notes.
    """
    notes = _load_notes_blob(emp_id, namespace)
    note_id = str(uuid.uuid4())
    now = datetime.now().isoformat(timespec="seconds")
    entry = {
        "id": note_id,
        "author": author,
        "author_name": author_name,
        "author_role": author_role,
        "week": week,
        "note": note,
        "created_at": now,
        "updated_at": now,
        "hidden": False,
    }
    notes.insert(0, entry)  # newest first
    _save_notes_blob(emp_id, notes, namespace)
    logger.info(
        "Note %s added for emp=%s week=%s ns=%s by %s",
        note_id, emp_id, week, namespace or "default", author,
    )
    return note_id


def get_notes(emp_id: str, week: str = "", limit: int = 50, namespace: str = "") -> List[Dict]:
    """Return coaching notes for an employee filtered by week, newest first.

    Only visible (non-hidden) notes for the given week are returned. ``namespace``
    selects an isolated note store (default = the Individual Report store).
    """
    notes = _load_notes_blob(emp_id, namespace)
    # Filter: non-hidden and matching week
    notes = [n for n in notes if not n.get("hidden", False)]
    if week:
        notes = [n for n in notes if n.get("week", "") == week]
    result = notes[:limit]
    return [
        {
            "id": n["id"],
            "author": n.get("author", "Unknown"),
            "author_name": n.get("author_name", ""),
            "author_role": n.get("author_role", ""),
            "week": n.get("week", ""),
            "note": n.get("note", ""),
            "date": n.get("created_at", ""),
            "updated_at": n.get("updated_at", ""),
        }
        for n in result
    ]


def edit_note(emp_id: str, note_id: str, author: str, new_text: str, namespace: str = "") -> bool:
    """Edit an existing note (only if author matches). Returns True if updated."""
    notes = _load_notes_blob(emp_id, namespace)
    for n in notes:
        if n["id"] == note_id and n.get("author") == author:
            n["note"] = new_text
            n["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_notes_blob(emp_id, notes, namespace)
            logger.info("Note %s edited for emp=%s by %s", note_id, emp_id, author)
            return True
    return False


def hide_note(emp_id: str, note_id: str, author: str, namespace: str = "") -> bool:
    """Soft-delete: mark a note as hidden (only if author matches). Returns True if updated."""
    notes = _load_notes_blob(emp_id, namespace)
    for n in notes:
        if n["id"] == note_id and n.get("author") == author:
            n["hidden"] = True
            n["updated_at"] = datetime.now().isoformat(timespec="seconds")
            _save_notes_blob(emp_id, notes, namespace)
            logger.info("Note %s hidden for emp=%s by %s", note_id, emp_id, author)
            return True
    return False
