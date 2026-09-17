# ============================================================================
# Local Authentication Database Module
# ============================================================================
# Manages the SQLite user store for password-based authentication.
# Passwords are hashed with PBKDF2-HMAC-SHA256 (260,000 iterations, 32-byte
# salt) — no plaintext or reversible representation is ever stored.
#
# Schema
# ------
#   users(username TEXT PK, password_hash TEXT, salt TEXT, role TEXT)
#
# USAGE:
#   from auth_db import add_user, verify_user, get_user, list_users
#       add_user("9032746", "s3cret!", role="coach")
#   ok = verify_user("9032746", "s3cret!")
#
# Migrating an existing auth.db
# ------------------------------
#   Run `python auth_db.py --migrate` once to add the role column and drop
#   the plaintext password column from older databases.
# ============================================================================

import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
from typing import List, Optional

from backend.config.logging import get_logger

logger = get_logger(__name__)

# Anchor auth.db inside the application/ directory regardless of CWD
# local_auth.py lives at application/backend/auth/ → parents[2] = application/
DEFAULT_DB = str(Path(__file__).resolve().parents[2] / "auth.db")

# PBKDF2 parameters — increase ITERATIONS in future if compute budget allows
_HASH_ALGO = "sha256"
_ITERATIONS = 260_000
_SALT_BYTES = 32


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_conn(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _hash_password(password: str, salt: str) -> str:
    """
    Return a hex-encoded PBKDF2-HMAC-SHA256 digest.

    Args:
        password: Plaintext password.
        salt: Hex-encoded random salt (stored alongside hash).

    Returns:
        Hex-encoded hash string.
    """
    dk = hashlib.pbkdf2_hmac(
        _HASH_ALGO,
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _ITERATIONS,
    )
    return dk.hex()


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------


def init_db(db_path: str = DEFAULT_DB) -> None:
    """Create the users table if it does not exist."""
    logger.debug("Initializing database at %s", db_path)
    conn = _get_conn(db_path)
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'coach'
            )
            """
        )
    conn.close()


def migrate_db(db_path: str = DEFAULT_DB) -> None:
    """
    One-time migration for older databases that stored plaintext passwords.
    Adds the role column (if missing) and drops the plaintext password column.
    Safe to run multiple times — skips steps that are already done.

    Args:
        db_path: Path to the SQLite database file.
    """
    logger.info("Running database migration on %s", db_path)
    conn = _get_conn(db_path)
    cursor = conn.execute("PRAGMA table_info(users)")
    columns = {row["name"] for row in cursor.fetchall()}

    with conn:
        if "role" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'coach'"
            )
            logger.info("Migration: added 'role' column")

        if "password" in columns:
            # SQLite does not support DROP COLUMN before 3.35 — rebuild the table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users_new (
                    username      TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    salt          TEXT NOT NULL,
                    role          TEXT NOT NULL DEFAULT 'coach'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO users_new (username, password_hash, salt, role)
                SELECT username, password_hash, salt, role FROM users
                """
            )
            conn.execute("DROP TABLE users")
            conn.execute("ALTER TABLE users_new RENAME TO users")
            logger.info("Migration: removed plaintext 'password' column")

    conn.close()
    logger.info("Migration complete for %s", db_path)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def add_user(
    username: str,
    password: str,
    role: str = "coach",
    db_path: str = DEFAULT_DB,
) -> None:
    """
    Insert or replace a user with a securely hashed password.

    Args:
        username: Unique username (e.g. employee_id as string).
        password: Plaintext password — hashed before storage, never persisted.
        role: Role assigned to the user ('manager', 'coach', 'agent').
        db_path: Path to the SQLite database file.
    """
    init_db(db_path)
    salt = secrets.token_hex(_SALT_BYTES)
    password_hash = _hash_password(password, salt)
    conn = _get_conn(db_path)
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO users (username, password_hash, salt, role)
            VALUES (?, ?, ?, ?)
            """,
            (str(username).strip(), password_hash, salt, role),
        )
    conn.close()
    logger.info("User added/updated: %s (role=%s)", username, role)


def get_user(username: str, db_path: str = DEFAULT_DB) -> Optional[dict]:
    """
    Retrieve a user record by username (without any secret material beyond
    what is needed for verification).

    Args:
        username: Username to look up.
        db_path: Path to the SQLite database file.

    Returns:
        Dict with keys username, password_hash, salt, role — or None.
    """
    init_db(db_path)
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT username, password_hash, salt, role FROM users WHERE username = ?",
        (str(username).strip(),),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {k: row[k] for k in row.keys()}


def verify_user(username: str, password: str, db_path: str = DEFAULT_DB) -> bool:
    """
    Verify a username/password pair against the stored hash.

    Args:
        username: Username to verify.
        password: Plaintext password attempt.
        db_path: Path to the SQLite database file.

    Returns:
        True if the credentials are correct, False otherwise.
    """
    user = get_user(username, db_path)
    if not user:
        logger.warning("Verify failed — user not found: %s", username)
        return False
    expected = user["password_hash"]
    salt = user["salt"]
    result = hmac.compare_digest(_hash_password(password, salt), expected)
    if result:
        logger.info("Password verified for user: %s", username)
    else:
        logger.warning("Invalid password attempt for user: %s", username)
    return result


def delete_user(username: str, db_path: str = DEFAULT_DB) -> None:
    """
    Delete a user from the database.

    Args:
        username: Username to delete.
        db_path: Path to the SQLite database file.
    """
    conn = _get_conn(db_path)
    with conn:
        conn.execute(
            "DELETE FROM users WHERE username = ?",
            (str(username).strip(),),
        )
    conn.close()


def list_users(db_path: str = DEFAULT_DB) -> List[str]:
    """
    Return a list of all usernames in the database.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List of username strings.
    """
    init_db(db_path)
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT username FROM users").fetchall()
    conn.close()
    return [r["username"] for r in rows]


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if "--migrate" in sys.argv:
        migrate_db()
    else:
        print("Usage: python auth_db.py --migrate")