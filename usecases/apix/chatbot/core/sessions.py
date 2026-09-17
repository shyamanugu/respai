"""
chatbot/core/sessions.py — Chat session storage (Azure Blob backed).
=====================================================================
Manages per-user chat sessions stored as JSON blobs:

    chat_sessions/{user_id}/{session_id}.json

Each session JSON:
{
    "session_id": "uuid",
    "user_id": 12345,
    "title": "Resolution rate for Badiang",
    "created_at": "2026-06-19T14:30:00Z",
    "updated_at": "2026-06-19T14:35:22Z",
    "messages": [
        {"role": "user", "content": "...", "timestamp": "..."},
        {"role": "bot", "content": "...", "timestamp": "..."}
    ]
}

Falls back to local file storage (chatbot/data/sessions/) when blob is
not configured.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chatbot.core.config import get_logger, PACKAGE_ROOT

log = get_logger(__name__)

# ── Blob configuration ─────────────────────────────────────────────────────
_CONN_STR = os.getenv("AZURE_BLOB_CONNECTION_STRING")
_CONTAINER = os.getenv("AZURE_BLOB_CONTAINER", "")
_PREFIX = "chat_sessions"

_container_client = None

if _CONN_STR and _CONTAINER:
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
        _blob_svc = BlobServiceClient.from_connection_string(_CONN_STR)
        _container_client = _blob_svc.get_container_client(_CONTAINER)
        log.info("Sessions blob storage configured: container=%s", _CONTAINER)
    except Exception as exc:
        log.warning("Blob storage unavailable, falling back to local: %s", exc)

# ── Local fallback dir ─────────────────────────────────────────────────────
_LOCAL_DIR = PACKAGE_ROOT / "data" / "sessions"
_LOCAL_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────

def _blob_path(user_id: int, session_id: str) -> str:
    return f"{_PREFIX}/{user_id}/{session_id}.json"


def _local_path(user_id: int, session_id: str) -> Path:
    user_dir = _LOCAL_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / f"{session_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _save_session(user_id: int, session: dict) -> None:
    """Persist a session dict to blob (or local fallback)."""
    session_id = session["session_id"]
    payload = json.dumps(session, ensure_ascii=False, indent=2)

    if _container_client:
        try:
            from azure.storage.blob import ContentSettings
            bc = _container_client.get_blob_client(_blob_path(user_id, session_id))
            bc.upload_blob(
                payload,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
            return
        except Exception as exc:
            log.error("Blob save failed for session %s: %s", session_id, exc)

    # Local fallback
    _local_path(user_id, session_id).write_text(payload, encoding="utf-8")


def _load_session(user_id: int, session_id: str) -> dict | None:
    """Load a single session."""
    if _container_client:
        try:
            from azure.core.exceptions import ResourceNotFoundError
            bc = _container_client.get_blob_client(_blob_path(user_id, session_id))
            raw = bc.download_blob().readall().decode("utf-8-sig")
            return json.loads(raw)
        except ResourceNotFoundError:
            pass
        except Exception as exc:
            log.error("Blob load failed for session %s: %s", session_id, exc)

    # Local fallback
    fp = _local_path(user_id, session_id)
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    return None


def _list_sessions_meta(user_id: int) -> list[dict]:
    """List session metadata (id, title, created_at, updated_at, msg count)."""
    sessions: list[dict] = []

    if _container_client:
        try:
            prefix = f"{_PREFIX}/{user_id}/"
            for blob in _container_client.list_blobs(name_starts_with=prefix):
                bc = _container_client.get_blob_client(blob.name)
                raw = bc.download_blob().readall().decode("utf-8-sig")
                data = json.loads(raw)
                sessions.append({
                    "session_id": data["session_id"],
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(data.get("messages", [])),
                })
            sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
            return sessions
        except Exception as exc:
            log.error("Blob list failed for user %d: %s", user_id, exc)

    # Local fallback
    user_dir = _LOCAL_DIR / str(user_id)
    if user_dir.exists():
        for fp in user_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": data["session_id"],
                    "title": data.get("title", "Untitled"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                continue
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions


# ── Public API ─────────────────────────────────────────────────────────────

def create_session(user_id: int, title: str = "") -> dict:
    """Create a new empty session and persist it. Returns session metadata."""
    session_id = str(uuid.uuid4())
    now = _now_iso()
    session = {
        "session_id": session_id,
        "user_id": user_id,
        "title": title or f"Chat {datetime.now(timezone.utc).strftime('%b %d, %H:%M')}",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    _save_session(user_id, session)
    log.info("create_session → user=%d, session=%s", user_id, session_id)
    return {
        "session_id": session_id,
        "title": session["title"],
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }


def add_message(
    user_id: int,
    session_id: str,
    role: str,
    content: str,
    ai_responded: bool = True,
    latency_ms: int | None = None,
) -> dict:
    """Append a message to a session. Returns the message entry.

    *ai_responded* – True when the bot message was a real AI answer,
    False when it was an error / fallback.
    *latency_ms* – end-to-end response time for a bot message (server-side
    ``elapsed_ms`` when available, else the client round-trip).
    """
    session = _load_session(user_id, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found for user {user_id}")

    ts = _now_iso()
    msg: dict[str, Any] = {"role": role, "content": content, "timestamp": ts}
    if role == "bot":
        msg["ai_responded"] = ai_responded
        if isinstance(latency_ms, (int, float)) and latency_ms >= 0:
            msg["latency_ms"] = int(latency_ms)
    session["messages"].append(msg)
    session["updated_at"] = ts

    # Auto-title from first user message if title is generic
    if role == "user" and session["title"].startswith("Chat "):
        first_words = content[:50].strip()
        if first_words:
            session["title"] = first_words + ("..." if len(content) > 50 else "")

    _save_session(user_id, session)
    return msg


def get_session(user_id: int, session_id: str) -> dict | None:
    """Load a full session with all messages."""
    return _load_session(user_id, session_id)


def list_sessions(user_id: int) -> list[dict]:
    """List all sessions for a user (metadata only, no messages)."""
    return _list_sessions_meta(user_id)


def recent_context_messages(
    user_id: int,
    current_session_id: str | None = None,
    max_sessions: int = 5,
    max_messages: int = 6,
) -> list[dict[str, str]]:
    """Return recent chat messages across a user's latest conversations.

    Connects to the user's folder (``{_PREFIX}/{user_id}/`` in the configured
    blob container, or the local fallback), pulls up to *max_sessions* sessions
    ordered by recency — always including the explicitly active
    *current_session_id* — merges their user/bot messages in chronological
    order and returns the trailing *max_messages* entries shaped for
    short-term memory as ``{"role": "user"|"assistant", "content": ...}``.
    """
    meta = _list_sessions_meta(user_id)  # sorted updated_at desc

    ordered_ids: list[str] = []
    if current_session_id:
        ordered_ids.append(current_session_id)
    for m in meta:
        sid = m.get("session_id")
        if sid and sid not in ordered_ids:
            ordered_ids.append(sid)
        if len(ordered_ids) >= max_sessions:
            break

    collected: list[tuple[str, str, str]] = []  # (timestamp, role, content)
    for sid in ordered_ids:
        sess = _load_session(user_id, sid)
        if not sess:
            continue
        for msg in sess.get("messages", []):
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "bot") and content:
                collected.append((msg.get("timestamp", ""), role, content))

    # Chronological (oldest → newest); keep only the trailing window.
    collected.sort(key=lambda t: t[0])
    if max_messages > 0:
        collected = collected[-max_messages:]

    out = [
        {"role": "assistant" if role == "bot" else "user", "content": content}
        for _ts, role, content in collected
    ]
    log.info(
        "recent_context_messages → user=%d sessions=%d msgs=%d (current=%s)",
        user_id, len(ordered_ids), len(out), current_session_id,
    )
    return out


def session_context_messages(
    user_id: int,
    session_id: str | None,
    max_messages: int = 6,
    exclude_query: str | None = None,
) -> list[dict[str, str]]:
    """Return the trailing chat messages of ONE specific session.

    Loads ``{_PREFIX}/{user_id}/{session_id}.json`` (blob or local fallback)
    and returns its user/bot messages in chronological order, truncated to the
    trailing *max_messages*, shaped for short-term memory as
    ``{"role": "user"|"assistant", "content": ...}``.

    This anchors follow-up context to the conversation the user actually
    selected — unlike :func:`recent_context_messages`, which blends several
    recent sessions and can surface stale turns from unrelated chats.

    *exclude_query* — when the most recent stored message is the same user
    question currently being processed (the frontend persists it just before
    calling ``/chat_agent``), it is dropped so it is not duplicated in history.
    Returns ``[]`` when *session_id* is falsy or the session is missing/empty.
    """
    if not session_id:
        return []
    sess = _load_session(user_id, session_id)
    if not sess:
        log.info(
            "session_context_messages → no session %s for user=%d", session_id, user_id
        )
        return []

    collected: list[tuple[str, str, str]] = []  # (timestamp, role, content)
    for msg in sess.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "bot") and content:
            collected.append((msg.get("timestamp", ""), role, content))

    # Chronological (oldest → newest).
    collected.sort(key=lambda t: t[0])

    # Drop a trailing user turn equal to the in-flight query (avoids the
    # current question appearing twice once the analyze node records it).
    if exclude_query and collected:
        _ts, last_role, last_content = collected[-1]
        if last_role == "user" and last_content.strip() == exclude_query.strip():
            collected.pop()

    if max_messages > 0:
        collected = collected[-max_messages:]

    out = [
        {"role": "assistant" if role == "bot" else "user", "content": content}
        for _ts, role, content in collected
    ]
    log.info(
        "session_context_messages → user=%d session=%s msgs=%d",
        user_id, session_id, len(out),
    )
    return out



def update_title(user_id: int, session_id: str, title: str) -> dict | None:
    """Rename a session. Returns updated metadata or None."""
    session = _load_session(user_id, session_id)
    if not session:
        return None
    session["title"] = title
    session["updated_at"] = _now_iso()
    _save_session(user_id, session)
    log.info("update_title → user=%d, session=%s, title=%s", user_id, session_id, title)
    return {
        "session_id": session_id,
        "title": session["title"],
        "updated_at": session["updated_at"],
    }


def delete_session(user_id: int, session_id: str) -> bool:
    """Delete a session. Returns True if deleted."""
    if _container_client:
        try:
            bc = _container_client.get_blob_client(_blob_path(user_id, session_id))
            bc.delete_blob()
            log.info("delete_session → blob deleted: user=%d, session=%s", user_id, session_id)
            return True
        except Exception as exc:
            log.error("Blob delete failed: %s", exc)

    fp = _local_path(user_id, session_id)
    if fp.exists():
        fp.unlink()
        log.info("delete_session → local deleted: user=%d, session=%s", user_id, session_id)
        return True
    return False


# ── Feedback (chat memory) ─────────────────────────────────────────────────
_FEEDBACK_PREFIX = "chat_memory"


def _feedback_blob_path(user_id: int) -> str:
    return f"{_FEEDBACK_PREFIX}/{user_id}/feedback.json"


def _feedback_local_path(user_id: int) -> Path:
    fb_dir = _LOCAL_DIR.parent / "chat_memory" / str(user_id)
    fb_dir.mkdir(parents=True, exist_ok=True)
    return fb_dir / "feedback.json"


def _load_feedback(user_id: int) -> list[dict]:
    """Load feedback entries for a user."""
    if _container_client:
        try:
            from azure.core.exceptions import ResourceNotFoundError
            bc = _container_client.get_blob_client(_feedback_blob_path(user_id))
            raw = bc.download_blob().readall().decode("utf-8-sig")
            return json.loads(raw)
        except ResourceNotFoundError:
            return []
        except Exception as exc:
            log.error("Feedback blob load failed: %s", exc)

    fp = _feedback_local_path(user_id)
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    return []


def _save_feedback(user_id: int, entries: list[dict]) -> None:
    """Persist feedback entries."""
    payload = json.dumps(entries, ensure_ascii=False, indent=2)
    if _container_client:
        try:
            from azure.storage.blob import ContentSettings
            bc = _container_client.get_blob_client(_feedback_blob_path(user_id))
            bc.upload_blob(
                payload,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )
            return
        except Exception as exc:
            log.error("Feedback blob save failed: %s", exc)

    _feedback_local_path(user_id).write_text(payload, encoding="utf-8")


def add_feedback(
    user_id: int,
    session_id: str,
    query: str,
    response: str,
    rating: str,
    comment: str = "",
) -> dict:
    """Store feedback for a bot response (upsert).

    If the user has already rated the same message (matched by
    ``session_id`` + ``query`` + ``response``), that entry is updated in place
    so the rating/comment can be edited. Otherwise a new entry is created.
    """
    entries = _load_feedback(user_id)
    stored_response = response[:500]

    existing = next(
        (
            e
            for e in entries
            if e.get("session_id") == session_id
            and e.get("query") == query
            and e.get("response") == stored_response
        ),
        None,
    )

    if existing is not None:
        existing["rating"] = rating
        existing["comment"] = comment
        existing["timestamp"] = _now_iso()
        entry = existing
        action = "update_feedback"
    else:
        entry = {
            "session_id": session_id,
            "query": query,
            "response": stored_response,
            "rating": rating,
            "comment": comment,
            "timestamp": _now_iso(),
        }
        entries.append(entry)
        action = "add_feedback"

    _save_feedback(user_id, entries)
    log.info("%s → user=%d, rating=%s, session=%s", action, user_id, rating, session_id)
    return entry


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD — aggregate chart data across all users (parallel I/O)
# ═══════════════════════════════════════════════════════════════════════════
# Both scans (sessions + feedback) run concurrently, and within each scan the
# individual blob downloads are fanned out across a thread pool so the
# dashboard stays fast even with many users/sessions.
_DASHBOARD_WORKERS = int(os.getenv("CHAT_DASHBOARD_WORKERS", "12"))


def _uid_from_path(name: str) -> Any:
    """Extract the ``{user_id}`` segment from a blob/file path (``…/<uid>/…``)."""
    parts = name.replace("\\", "/").split("/")
    for i, p in enumerate(parts[:-1]):
        if p in (_PREFIX, _FEEDBACK_PREFIX) and i + 1 < len(parts):
            seg = parts[i + 1]
            return int(seg) if seg.isdigit() else seg
    return None


def _download_blob_json(blob_name: str) -> Any:
    """Download and parse a single JSON blob (used by the thread pool)."""
    try:
        bc = _container_client.get_blob_client(blob_name)
        raw = bc.download_blob().readall().decode("utf-8-sig")
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — one bad blob shouldn't fail the scan
        log.error("dashboard: download failed for %s: %s", blob_name, exc)
        return None


def _all_session_docs() -> list[dict]:
    """Return every session document across all users (parallel download)."""
    docs: list[dict] = []
    if _container_client:
        try:
            names = [
                b.name for b in _container_client.list_blobs(name_starts_with=f"{_PREFIX}/")
                if b.name.endswith(".json")
            ]
            with ThreadPoolExecutor(max_workers=_DASHBOARD_WORKERS) as ex:
                for name, doc in zip(names, ex.map(_download_blob_json, names)):
                    if isinstance(doc, dict):
                        doc.setdefault("user_id", _uid_from_path(name))
                        docs.append(doc)
            return docs
        except Exception as exc:  # noqa: BLE001
            log.error("dashboard: session blob scan failed: %s", exc)

    # Local fallback — sessions/<uid>/<sid>.json
    for fp in _LOCAL_DIR.glob("*/*.json"):
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc.setdefault("user_id", _uid_from_path(str(fp)))
                docs.append(doc)
        except Exception:  # noqa: BLE001
            continue
    return docs


def _all_feedback_entries() -> list[dict]:
    """Return every feedback entry across all users (parallel download)."""
    entries: list[dict] = []

    def _flatten(name: str, data: Any) -> None:
        if isinstance(data, list):
            uid = _uid_from_path(name)
            for e in data:
                if isinstance(e, dict):
                    e.setdefault("user_id", uid)
                    entries.append(e)

    if _container_client:
        try:
            names = [
                b.name for b in _container_client.list_blobs(name_starts_with=f"{_FEEDBACK_PREFIX}/")
                if b.name.endswith(".json")
            ]
            with ThreadPoolExecutor(max_workers=_DASHBOARD_WORKERS) as ex:
                for name, data in zip(names, ex.map(_download_blob_json, names)):
                    _flatten(name, data)
            return entries
        except Exception as exc:  # noqa: BLE001
            log.error("dashboard: feedback blob scan failed: %s", exc)

    # Local fallback — chat_memory/<uid>/feedback.json
    fb_root = _LOCAL_DIR.parent / "chat_memory"
    if fb_root.exists():
        for fp in fb_root.glob("*/feedback.json"):
            try:
                _flatten(str(fp), json.loads(fp.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue
    return entries


def _series(counter: dict[str, int]) -> dict[str, list]:
    """Turn a {date: count} map into sorted ``{labels, counts}`` chart data."""
    labels = sorted(counter)
    return {"labels": labels, "counts": [counter[d] for d in labels]}


# Bot responses slower than this (seconds) are flagged as "slow" — aligned with
# the user-perceived latency budget that previously triggered frontend timeouts.
_SLOW_THRESHOLD_S: float = float(os.getenv("CHAT_SLOW_RESPONSE_S", "10"))


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list (empty → 0.0)."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round((pct / 100.0) * len(sorted_vals) + 0.5)) - 1))
    return sorted_vals[k]


def _aggregate_dashboard(session_docs: list[dict], feedback: list[dict]) -> dict:
    """Build chart-ready aggregates from raw sessions + feedback."""
    from collections import Counter, defaultdict

    users: set = set()
    total_messages = 0
    ai_ok = ai_fail = 0
    sessions_per_day: dict[str, int] = defaultdict(int)
    messages_per_day: dict[str, int] = defaultdict(int)
    sessions_per_user: Counter = Counter()
    msgs_counts: list[int] = []

    # Latency tracking (bot messages only, in milliseconds).
    latencies: list[float] = []
    lat_sum_per_day: dict[str, float] = defaultdict(float)
    lat_cnt_per_day: dict[str, int] = defaultdict(int)
    # Distribution buckets (seconds): <1, 1-3, 3-5, 5-10, 10-30, >30.
    lat_buckets = [0, 0, 0, 0, 0, 0]
    slow_responses = 0  # > SLOW_THRESHOLD_S
    slowest = 0.0

    for doc in session_docs:
        uid = doc.get("user_id")
        if uid is not None:
            users.add(uid)
            sessions_per_user[str(uid)] += 1
        created = (doc.get("created_at") or "")[:10]
        if created:
            sessions_per_day[created] += 1
        msgs = doc.get("messages", []) or []
        msgs_counts.append(len(msgs))
        for m in msgs:
            total_messages += 1
            day = (m.get("timestamp") or created or "")[:10]
            if day:
                messages_per_day[day] += 1
            if m.get("role") == "bot":
                if m.get("ai_responded", True):
                    ai_ok += 1
                else:
                    ai_fail += 1
                lat = m.get("latency_ms")
                if isinstance(lat, (int, float)) and lat >= 0:
                    lat_ms = float(lat)
                    latencies.append(lat_ms)
                    slowest = max(slowest, lat_ms)
                    if day:
                        lat_sum_per_day[day] += lat_ms
                        lat_cnt_per_day[day] += 1
                    secs = lat_ms / 1000.0
                    if secs < 1:
                        lat_buckets[0] += 1
                    elif secs < 3:
                        lat_buckets[1] += 1
                    elif secs < 5:
                        lat_buckets[2] += 1
                    elif secs < 10:
                        lat_buckets[3] += 1
                    elif secs < 30:
                        lat_buckets[4] += 1
                    else:
                        lat_buckets[5] += 1
                    if secs > _SLOW_THRESHOLD_S:
                        slow_responses += 1

    up = sum(1 for f in feedback if f.get("rating") == "up")
    down = sum(1 for f in feedback if f.get("rating") == "down")
    fb_up_day: dict[str, int] = defaultdict(int)
    fb_down_day: dict[str, int] = defaultdict(int)
    for f in feedback:
        if f.get("user_id") is not None:
            users.add(f["user_id"])
        day = (f.get("timestamp") or "")[:10]
        if not day:
            continue
        if f.get("rating") == "up":
            fb_up_day[day] += 1
        elif f.get("rating") == "down":
            fb_down_day[day] += 1

    top = sessions_per_user.most_common(10)
    fb_days = sorted(set(fb_up_day) | set(fb_down_day))
    recent = sorted(feedback, key=lambda f: f.get("timestamp", ""), reverse=True)[:20]

    fb_total = up + down
    bot_total = ai_ok + ai_fail
    n_sessions = len(session_docs)

    # Latency stats.
    lat_sorted = sorted(latencies)
    avg_lat = round(sum(latencies) / len(latencies)) if latencies else 0
    p50_lat = round(_percentile(lat_sorted, 50))
    p95_lat = round(_percentile(lat_sorted, 95))
    p99_lat = round(_percentile(lat_sorted, 99))
    lat_days = sorted(lat_cnt_per_day)
    lat_trend = {
        "labels": lat_days,
        "avg_ms": [round(lat_sum_per_day[d] / lat_cnt_per_day[d]) for d in lat_days],
    }
    slow_pct = round(100 * slow_responses / len(latencies), 1) if latencies else 0.0

    return {
        "kpis": {
            "users": len(users),
            "sessions": n_sessions,
            "messages": total_messages,
            "feedback": fb_total,
            "thumbs_up": up,
            "thumbs_down": down,
            "satisfaction_pct": round(100 * up / fb_total, 1) if fb_total else 0.0,
            "ai_success_pct": round(100 * ai_ok / bot_total, 1) if bot_total else 0.0,
            "avg_msgs_per_session": round(sum(msgs_counts) / n_sessions, 1) if n_sessions else 0.0,
            "avg_latency_ms": avg_lat,
            "p95_latency_ms": p95_lat,
            "p99_latency_ms": p99_lat,
            "slow_responses": slow_responses,
            "slow_pct": slow_pct,
            "slow_threshold_s": _SLOW_THRESHOLD_S,
            "measured_responses": len(latencies),
        },
        "sessions_per_day": _series(sessions_per_day),
        "messages_per_day": _series(messages_per_day),
        "feedback_breakdown": {"up": up, "down": down},
        "feedback_per_day": {
            "labels": fb_days,
            "up": [fb_up_day[d] for d in fb_days],
            "down": [fb_down_day[d] for d in fb_days],
        },
        "latency_summary": {
            "avg_ms": avg_lat,
            "p50_ms": p50_lat,
            "p95_ms": p95_lat,
            "p99_ms": p99_lat,
            "slowest_ms": round(slowest),
            "measured": len(latencies),
        },
        "latency_trend": lat_trend,
        "latency_distribution": {
            "labels": ["<1s", "1-3s", "3-5s", "5-10s", "10-30s", ">30s"],
            "counts": lat_buckets,
        },
        "latency_percentiles": {
            "labels": ["Avg", "P50", "P95", "P99"],
            "counts": [avg_lat, p50_lat, p95_lat, p99_lat],
        },
        "top_users": {
            "labels": [u for u, _ in top],
            "counts": [c for _, c in top],
        },
        "ai_response": {"responded": ai_ok, "failed": ai_fail},
        "recent_feedback": [
            {
                "user_id": f.get("user_id"),
                "rating": f.get("rating", ""),
                "query": (f.get("query") or "")[:120],
                "comment": (f.get("comment") or "")[:160],
                "timestamp": f.get("timestamp", ""),
            }
            for f in recent
        ],
        "generated_at": _now_iso(),
    }


async def dashboard_data() -> dict:
    """Aggregate dashboard chart data, scanning sessions + feedback in parallel."""
    session_docs, feedback = await asyncio.gather(
        asyncio.to_thread(_all_session_docs),
        asyncio.to_thread(_all_feedback_entries),
    )
    data = _aggregate_dashboard(session_docs, feedback)
    log.info(
        "dashboard_data → users=%d sessions=%d messages=%d feedback=%d "
        "avg_latency=%dms p95=%dms slow=%d",
        data["kpis"]["users"], data["kpis"]["sessions"],
        data["kpis"]["messages"], data["kpis"]["feedback"],
        data["kpis"]["avg_latency_ms"], data["kpis"]["p95_latency_ms"],
        data["kpis"]["slow_responses"],
    )
    return data

