"""
services/blob_service.py — Azure Blob Storage data access layer
================================================================
All Azure Blob read operations, index loading, report loading,
and week discovery.
"""

import json
import os
import re
from typing import List, Optional

import streamlit as st
from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv

from backend.services.normalizer import normalize_report
from backend.config.logging import get_logger
from backend.config.programs import get_cache_ttl

logger = get_logger(__name__)

load_dotenv()


def _resolve_blob_prefix() -> Optional[str]:
    """Return the blob prefix for the active program, or None for legacy paths."""
    try:
        from backend.config.programs import load_program_config
        import streamlit as _st
        program_id = _st.session_state.get("selected_program")
        if program_id:
            cfg = load_program_config(program_id)
            return cfg.blob_prefix
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# Azure Blob connection (module-level singletons)
# ---------------------------------------------------------------------------
AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING")
AZURE_BLOB_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER")
DEFAULT_INDEX_PATH = os.getenv("INDEX_BLOB_PATH", "index/2025-08-28.json")
DEFAULT_REPORTS_PREFIX = os.getenv("REPORTS_PREFIX", "2025-08-28")

if AZURE_BLOB_CONNECTION_STRING and AZURE_BLOB_CONTAINER:
    blob_service = BlobServiceClient.from_connection_string(AZURE_BLOB_CONNECTION_STRING)
    container_client = blob_service.get_container_client(AZURE_BLOB_CONTAINER)
else:
    blob_service = None
    container_client = None


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def extract_json_text(text: str) -> str:
    """Extract JSON from text that might contain markdown code blocks"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.I)
    if m:
        return m.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    ends = [text.rfind("}"), text.rfind("]")]
    if starts and max(ends) > min(starts):
        return text[min(starts):max(ends)+1].strip()
    return text


def safe_load_json_text(raw: str):
    """Safely load JSON from raw text"""
    if not raw:
        return None
    try:
        return json.loads(extract_json_text(raw))
    except Exception:
        return None


def get_blob_text(blob_path: str):
    """Get text content from Azure blob"""
    from azure.core.exceptions import ResourceNotFoundError
    # Normalize to "dir/file.json" (no leading/trailing slashes or backslashes)
    p = "/".join(s.strip("/\\") for s in blob_path.split("/"))
    try:
        bc = container_client.get_blob_client(p)
        data = bc.download_blob().readall().decode("utf-8-sig")
        logger.debug("Blob fetched: %s", p)
        return data
    except ResourceNotFoundError:
        logger.debug("Blob not found: %s", p)
        return None
    except Exception as exc:
        logger.error("Blob read error for %s: %s", p, exc)
        return None


# ---------------------------------------------------------------------------
# Week / index discovery
# ---------------------------------------------------------------------------

@st.cache_data(ttl=600)
def discover_available_weeks(_program_prefix: Optional[str] = None) -> List[str]:
    """
    Discover available week folders in Azure Blob Storage.
    When *_program_prefix* is set, only looks under that prefix.
    Returns week folder names sorted descending.
    """
    prefix = _program_prefix or _resolve_blob_prefix()
    week_folders = set()
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')

    try:
        blob_prefix = f"{prefix}/" if prefix else ""
        for blob in container_client.list_blobs(name_starts_with=blob_prefix or None):
            parts = blob.name.split('/')
            # With prefix: prefix/2025-08-28/... → parts[1] is the date
            # Without prefix: 2025-08-28/... → parts[0] is the date
            idx = 1 if prefix else 0
            if len(parts) > idx:
                folder_name = parts[idx]
                if date_pattern.match(folder_name):
                    week_folders.add(folder_name)
    except Exception as e:
        logger.error("Failed to discover week folders: %s", e)
        st.warning(f"Could not discover week folders: {e}")
        return []

    result = sorted(list(week_folders), reverse=True)
    logger.info("Discovered %d available weeks", len(result))
    return result


@st.cache_data(ttl=600)
def discover_index_files(_program_prefix: Optional[str] = None) -> List[str]:
    """
    Discover available index files in the index/ folder.
    Scoped to *_program_prefix* when set.
    """
    prefix = _program_prefix or _resolve_blob_prefix()
    index_files = []
    date_pattern = re.compile(r'(\d{4}-\d{2}-\d{2})')
    blob_prefix = f"{prefix}/index/" if prefix else "index/"

    try:
        for blob in container_client.list_blobs(name_starts_with=blob_prefix):
            if blob.name.endswith('.json'):
                index_files.append(blob.name)
    except Exception as e:
        st.warning(f"Could not discover index files: {e}")
        return []

    def extract_date(path):
        match = date_pattern.search(path)
        return match.group(1) if match else "0000-00-00"

    return sorted(index_files, key=extract_date, reverse=True)


def get_selected_week() -> str:
    """Get the currently selected week from session state, defaulting to most recent."""
    if 'selected_week' not in st.session_state:
        weeks = discover_available_weeks()
        st.session_state.selected_week = weeks[0] if weeks else DEFAULT_REPORTS_PREFIX
    return st.session_state.selected_week


def get_index_path_for_week(week: str) -> str:
    """Get the appropriate index file path for a given week."""
    index_files = discover_index_files()
    for idx_file in index_files:
        if week in idx_file:
            return idx_file
    return index_files[0] if index_files else DEFAULT_INDEX_PATH


# ---------------------------------------------------------------------------
# Index / report loading
# ---------------------------------------------------------------------------

def _flatten_index(data) -> List[dict]:
    """Flatten either nested or flat index JSON into a uniform list."""
    flattened = []

    if isinstance(data, dict):
        for coach_id, coach_info in data.items():
            if not isinstance(coach_info, dict):
                continue
            coach_name = coach_info.get("CoachName") or str(coach_id).strip()
            employees = coach_info.get("employees") or []
            for emp in employees:
                emp_id = emp.get("EmployeeID")
                emp_name = emp.get("EmployeeName")
                if emp_id and emp_name:
                    flattened.append({
                        "EmployeeID": str(emp_id).strip(),
                        "EmployeeName": str(emp_name).strip(),
                        "CoachID": str(coach_id).strip(),
                        "CoachName": str(coach_name).strip(),
                    })

    elif isinstance(data, list):
        for emp in data:
            if not isinstance(emp, dict):
                continue
            emp_id = emp.get("EmployeeID")
            emp_name = emp.get("EmployeeName")
            if emp_id and emp_name:
                flattened.append({
                    "EmployeeID": str(emp_id).strip(),
                    "EmployeeName": str(emp_name).strip(),
                    "CoachID": None,
                    "CoachName": None,
                })

    return flattened


@st.cache_data(ttl=get_cache_ttl())
def load_index_for_week(week: str = None) -> List[dict]:
    """
    Load the employee index for a specific week.
    Falls back to the most recent index if not found.
    """
    if week is None:
        week = get_selected_week()

    index_path = get_index_path_for_week(week)
    raw = get_blob_text(index_path)
    data = safe_load_json_text(raw)
    if not data:
        return []

    return _flatten_index(data)


@st.cache_data(ttl=get_cache_ttl())
def load_index() -> List[dict]:
    """Load the default index."""
    raw = get_blob_text(DEFAULT_INDEX_PATH)
    data = safe_load_json_text(raw)
    if not data:
        return []

    return _flatten_index(data)


@st.cache_data(ttl=get_cache_ttl())
def load_report_for_week(emp_id: str, week: str = None) -> Optional[dict]:
    """Load employee report from Azure Blob for a specific week and normalize it.

    Tries program-prefixed paths first (e.g. ``telesales/2025-08-28/9043045.json``),
    then falls back to legacy flat paths (``2025-08-28/9043045.json``).
    """
    if week is None:
        week = get_selected_week()

    prefix = _resolve_blob_prefix()

    # Build list of potential paths to try for this specific week
    paths_to_try: List[str] = []

    # Program-prefixed paths (when a prefix is active)
    if prefix:
        paths_to_try.extend([
            f"{prefix}/{week}/{emp_id}.json",
            f"{prefix}/{week}/{emp_id}.JSON",
        ])
        if not str(emp_id).startswith('e'):
            paths_to_try.append(f"{prefix}/{week}/e{emp_id}.json")
            paths_to_try.append(f"{prefix}/{week}/e{emp_id}.JSON")
        paths_to_try.extend([
            f"{prefix}/{week}/reports/{emp_id}.json",
            f"{prefix}/reports/{week}/{emp_id}.json",
        ])

    # Legacy flat paths (always tried as fallback)
    paths_to_try.extend([
        f"{week}/{emp_id}.json",
        f"{week}/{emp_id}.JSON",
    ])

    if not str(emp_id).startswith('e'):
        paths_to_try.append(f"{week}/e{emp_id}.json")
        paths_to_try.append(f"{week}/e{emp_id}.JSON")

    paths_to_try.extend([
        f"{week}/reports/{emp_id}.json",
        f"{week}/reports/{emp_id}.JSON",
        f"reports/{week}/{emp_id}.json",
        f"reports/{week}/{emp_id}.JSON",
    ])

    raw = None
    used_path = None
    for path in paths_to_try:
        raw = get_blob_text(path)
        if raw:
            used_path = path
            break

    if not raw:
        logger.warning("Report not found for emp=%s week=%s", emp_id, week)
        st.warning(f"Report not found for employee {emp_id} in week {week}")
        return None

    raw_report = safe_load_json_text(raw)
    if not raw_report:
        logger.error("Invalid JSON from %s", used_path)
        st.error(f"Failed to parse JSON from {used_path}")
        return None

    try:
        logger.info("Loaded report for emp=%s week=%s path=%s", emp_id, week, used_path)
        return normalize_report(raw_report)
    except Exception as e:
        logger.error("Normalization error for %s: %s", used_path, e)
        st.error(f"Error normalizing report: {e}")
        return raw_report


@st.cache_data(ttl=get_cache_ttl())
def load_report(emp_id: str) -> Optional[dict]:
    """Load employee report from Azure Blob and normalize it (default prefix)"""
    paths_to_try = [
        f"{DEFAULT_REPORTS_PREFIX}/{emp_id}.json",
        f"{DEFAULT_REPORTS_PREFIX}/{emp_id}.JSON",
    ]

    if not str(emp_id).startswith('e'):
        paths_to_try.append(f"{DEFAULT_REPORTS_PREFIX}/e{emp_id}.json")
        paths_to_try.append(f"{DEFAULT_REPORTS_PREFIX}/e{emp_id}.JSON")

    # Try in latest week subdirectory
    try:
        week_folders = []
        for blob in container_client.list_blobs(name_starts_with=f"{DEFAULT_REPORTS_PREFIX}/week_"):
            folder_name = blob.name.split('/')[1] if '/' in blob.name else None
            if folder_name and folder_name.startswith('week_') and folder_name not in week_folders:
                week_folders.append(folder_name)

        if week_folders:
            week_folders.sort(reverse=True)
            latest_week = week_folders[0]
            paths_to_try.append(f"{DEFAULT_REPORTS_PREFIX}/{latest_week}/{emp_id}.json")
            paths_to_try.append(f"{DEFAULT_REPORTS_PREFIX}/{latest_week}/{emp_id}.JSON")
            if not str(emp_id).startswith('e'):
                paths_to_try.append(f"{DEFAULT_REPORTS_PREFIX}/{latest_week}/e{emp_id}.json")
                paths_to_try.append(f"{DEFAULT_REPORTS_PREFIX}/{latest_week}/e{emp_id}.JSON")
    except Exception:
        pass

    raw = None
    used_path = None
    for path in paths_to_try:
        raw = get_blob_text(path)
        if raw:
            used_path = path
            break

    if not raw:
        logger.warning("Report not found for emp=%s (default prefix)", emp_id)
        st.warning(f"Report file not found. Tried: {', '.join(paths_to_try[:4])}")
        try:
            available_reports = []
            for blob in container_client.list_blobs(name_starts_with=f"{DEFAULT_REPORTS_PREFIX}/"):
                if blob.name.endswith('.json') or blob.name.endswith('.JSON'):
                    available_reports.append(blob.name)
            if available_reports:
                st.info(f"Available reports: {', '.join(available_reports[:10])}")
        except Exception:
            pass
        return None

    raw_report = safe_load_json_text(raw)
    if not raw_report:
        logger.error("Invalid JSON from %s (default prefix)", used_path)
        st.error(f"Failed to parse JSON from {used_path}")
        return None

    try:
        logger.info("Loaded report for emp=%s path=%s", emp_id, used_path)
        return normalize_report(raw_report)
    except Exception as e:
        logger.error("Normalization error for %s: %s", used_path, e)
        st.error(f"Error normalizing report: {e}")
        return None
