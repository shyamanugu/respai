
import json
from pathlib import Path
from typing import Dict, List, Optional

from backend.config.logging import get_logger
from backend.config.settings import settings

logger = get_logger(__name__)


def resolve_user(
    upn: Optional[str] = None,
    employee_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Look up a user record by UPN or employee_id.

    At least one of the two parameters must be provided.  UPN is tried
    first when both are supplied.

    Args:
        upn: User Principal Name (email) to search for.
        employee_id: Numeric employee ID to search for.

    Returns:
        A dict with keys ``name``, ``employee_id``, ``role``, and
        optionally ``coach_ids``; or None if no match is found.
    """
    upn_index, eid_index = _build_indexes()

    if upn:
        record = upn_index.get(upn.strip().lower())
        if record:
            logger.debug("Resolved user by UPN: %s -> eid=%s", upn, record["employee_id"])
            return record

    if employee_id is not None:
        record = eid_index.get(employee_id)
        if record:
            logger.debug("Resolved user by employee_id: %s -> %s", employee_id, record["name"])
            return record

    logger.debug("User not found: upn=%s, employee_id=%s", upn, employee_id)
    return None


# ---------------------------------------------------------------------------
# Backward-compatible helpers used by existing call sites
# ---------------------------------------------------------------------------


def resolve_employee_id(upn: str) -> Optional[int]:
    """
    Look up the employee_id for a UPN.

    Args:
        upn: User Principal Name (email).

    Returns:
        Integer employee_id, or None if the UPN is not in the map.
    """
    record = resolve_user(upn=upn)
    return record["employee_id"] if record else None


def resolve_coach_ids(upn: str) -> List[int]:
    """
    Look up the coach employee_ids a manager oversees, by UPN.

    Args:
        upn: User Principal Name of the manager.

    Returns:
        List of coach employee_ids, or an empty list.
    """
    record = resolve_user(upn=upn)
    if record:
        return record.get("coach_ids", [])
    return []


def get_all_managers() -> List[dict]:
    """
    Return all manager records that have non-empty coach_ids.

    Used to populate the superuser "Select Manager" dropdown.

    Returns:
        List of dicts with keys ``name``, ``employee_id``, ``role``, ``coach_ids``.
    """
    _, eid_index = _build_indexes()
    return [
        rec for rec in eid_index.values()
        if rec.get("role") == "manager" and rec.get("coach_ids")
    ]


# ---------------------------------------------------------------------------
# Internal loader
# ---------------------------------------------------------------------------


def _build_indexes() -> tuple:
    """
    Load the JSON file and build two lookup dicts.

    Returns:
        A tuple ``(upn_index, eid_index)`` where:
        - ``upn_index`` maps lowercase UPN strings to user records.
        - ``eid_index`` maps integer employee_ids to user records.
    """
    users = _load_users()

    upn_index: Dict[str, dict] = {}
    eid_index: Dict[int, dict] = {}

    for entry in users:
        record = {
            "name": entry.get("name", ""),
            "employee_id": int(entry["employee_id"]),
            "role": entry.get("role", "coach"),
            "coach_ids": [int(c) for c in entry.get("coach_ids", [])],
            "program": entry.get("program"),
        }

        # Index by every UPN alias
        for u in entry.get("upns", []):
            upn_index[u.strip().lower()] = record

        # Index by employee_id
        eid_index[record["employee_id"]] = record

    return upn_index, eid_index



def get_upn_map_from_blob() -> Optional[dict]:
    """
    Download the UPN map from Azure Blob Storage.

    Returns:
        Parsed JSON dict with 'users' key, or None if not found or invalid.
    """
    from backend.services.blob_service import get_blob_text
    raw = get_blob_text(settings.UPN_MAP_BLOB_PATH)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        # Validate it has the expected structure
        if isinstance(data, dict) and "users" in data:
            return data
        return None
    except (json.JSONDecodeError, ValueError):
        return None


def _load_users() -> list:
    """
    Load the user-mapping list from a local JSON file, falling back to
    Azure Blob Storage when the local file is missing.

    Returns:
        A list of user dicts (each with ``name``, ``employee_id``,
        ``upns``, ``role``, and optionally ``coach_ids``).
    """
    # 1. Try local file first
    local_path = Path(settings.UPN_MAP_PATH)
    if local_path.is_file():
        try:
            raw = local_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and "users" in data:
                return data["users"]
        except (json.JSONDecodeError, KeyError):
            pass

    # 2. Fallback: download from Azure Blob
    blob_data = get_upn_map_from_blob()
    if blob_data and "users" in blob_data:
        return blob_data["users"]

    return []
