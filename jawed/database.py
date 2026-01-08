"""Database management for channel-specific SQLite databases."""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jawed.definitions import CHANNEL_DB_PREFIX, DATA_DIR, MASTER_DB_NAME

logger = logging.getLogger(__name__)


def get_data_dir() -> Path:
    """Get the data directory path, creating it if necessary."""
    data_dir = Path(DATA_DIR)
    data_dir.mkdir(exist_ok=True)
    return data_dir


def get_master_db_path() -> Path:
    """Get the path to the master database."""
    return get_data_dir() / MASTER_DB_NAME


def get_channel_db_path(channel_id: str) -> Path:
    """Get the path to a channel-specific database."""
    safe_channel_id = channel_id.replace("/", "_").replace("\\", "_")
    return get_data_dir() / f"{CHANNEL_DB_PREFIX}{safe_channel_id}.db"


@contextmanager
def get_db_connection(db_path: Path):
    """Context manager for database connections."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_master_db() -> None:
    """Initialize the master database with required tables."""
    db_path = get_master_db_path()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                channel_name TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        logger.info(f"Master database initialized at {db_path}")


def init_channel_db(channel_id: str) -> None:
    """Initialize a channel-specific database with required tables."""
    db_path = get_channel_db_path(channel_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Channel configuration and OAuth tokens
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS channel_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                live_chat_id TEXT,
                accepting_requests_start_datetime TEXT,
                accepting_requests_end_datetime TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Song/video requests
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY,
                requesting_username TEXT NOT NULL,
                youtube_link TEXT NOT NULL,
                youtube_link_title TEXT,
                user_message TEXT,
                status TEXT DEFAULT 'pending',
                chat_message_id TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            )
        """)

        logger.info(f"Channel database initialized for {channel_id} at {db_path}")


def register_channel_in_master(channel_id: str, channel_name: str) -> dict[str, Any]:
    """Register a channel in the master database."""
    now = datetime.now(UTC).isoformat()
    db_path = get_master_db_path()

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO channels (channel_id, channel_name, is_active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                channel_name = excluded.channel_name,
                is_active = 1,
                updated_at = excluded.updated_at
            """,
            (channel_id, channel_name, now, now),
        )

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "is_active": True,
        "created_at": now,
    }


def get_channel_from_master(channel_id: str) -> dict[str, Any] | None:
    """Get channel info from master database."""
    db_path = get_master_db_path()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_all_active_channels() -> list[dict[str, Any]]:
    """Get all active channels from master database."""
    db_path = get_master_db_path()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channels WHERE is_active = 1")
        return [dict(row) for row in cursor.fetchall()]


def save_channel_config(
    channel_id: str,
    channel_name: str,
    live_chat_id: str | None = None,
    accepting_requests_start_datetime: str | None = None,
    accepting_requests_end_datetime: str | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_expiry: str | None = None,
) -> dict[str, Any] | None:
    """Save or update channel configuration in the channel-specific database."""
    now = datetime.now(UTC).isoformat()
    db_path = get_channel_db_path(channel_id)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()

        # Check if config exists
        cursor.execute("SELECT id FROM channel_config WHERE id = 1")
        exists = cursor.fetchone() is not None

        if exists:
            cursor.execute(
                """
                UPDATE channel_config SET
                    channel_name = ?,
                    live_chat_id = COALESCE(?, live_chat_id),
                    accepting_requests_start_datetime = COALESCE(?, accepting_requests_start_datetime),
                    accepting_requests_end_datetime = ?,
                    access_token = COALESCE(?, access_token),
                    refresh_token = COALESCE(?, refresh_token),
                    token_expiry = COALESCE(?, token_expiry),
                    updated_at = ?
                WHERE id = 1
                """,
                (
                    channel_name,
                    live_chat_id,
                    accepting_requests_start_datetime,
                    accepting_requests_end_datetime,
                    access_token,
                    refresh_token,
                    token_expiry,
                    now,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO channel_config (
                    id, channel_id, channel_name, live_chat_id,
                    accepting_requests_start_datetime, accepting_requests_end_datetime,
                    access_token, refresh_token, token_expiry,
                    created_at, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    channel_name,
                    live_chat_id,
                    accepting_requests_start_datetime,
                    accepting_requests_end_datetime,
                    access_token,
                    refresh_token,
                    token_expiry,
                    now,
                    now,
                ),
            )

    return get_channel_config(channel_id)


def get_channel_config(channel_id: str) -> dict[str, Any] | None:
    """Get channel configuration from channel-specific database."""
    db_path = get_channel_db_path(channel_id)
    if not db_path.exists():
        return None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channel_config WHERE id = 1")
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def is_channel_accepting_requests(channel_id: str) -> bool:
    """Check if a channel is currently accepting requests."""
    config = get_channel_config(channel_id)
    if not config:
        return False

    start_datetime = config.get("accepting_requests_start_datetime")
    end_datetime = config.get("accepting_requests_end_datetime")

    if not start_datetime:
        return False

    now = datetime.now(UTC)
    start = datetime.fromisoformat(start_datetime)

    # Start must be <= NOW
    if start > now:
        return False

    # End must be NULL (not set)
    return end_datetime is None


def save_request(
    channel_id: str,
    request_id: str,
    requesting_username: str,
    youtube_link: str,
    youtube_link_title: str | None = None,
    user_message: str | None = None,
) -> dict[str, Any]:
    """Save a new request to the channel database."""
    now = datetime.now(UTC).isoformat()
    db_path = get_channel_db_path(channel_id)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO requests (
                request_id, requesting_username, youtube_link,
                youtube_link_title, user_message, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (request_id, requesting_username, youtube_link, youtube_link_title, user_message, now),
        )

    return {
        "request_id": request_id,
        "requesting_username": requesting_username,
        "youtube_link": youtube_link,
        "youtube_link_title": youtube_link_title,
        "user_message": user_message,
        "status": "pending",
        "created_at": now,
    }


def update_request_status(
    channel_id: str,
    request_id: str,
    status: str,
    chat_message_id: str | None = None,
) -> dict[str, Any] | None:
    """Update request status after processing."""
    now = datetime.now(UTC).isoformat()
    db_path = get_channel_db_path(channel_id)

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE requests SET
                status = ?,
                chat_message_id = COALESCE(?, chat_message_id),
                processed_at = ?
            WHERE request_id = ?
            """,
            (status, chat_message_id, now, request_id),
        )

        cursor.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_request(channel_id: str, request_id: str) -> dict[str, Any] | None:
    """Get a specific request by ID."""
    db_path = get_channel_db_path(channel_id)
    if not db_path.exists():
        return None

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_channel_requests(
    channel_id: str,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get requests for a channel, optionally filtered by status."""
    db_path = get_channel_db_path(channel_id)
    if not db_path.exists():
        return []

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "SELECT * FROM requests WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor.execute("SELECT * FROM requests ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]


# API User management
def create_api_user(user_id: str, username: str, password_hash: str, is_admin: bool = False) -> dict[str, Any]:
    """Create a new API user."""
    now = datetime.now(UTC).isoformat()
    db_path = get_master_db_path()

    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO api_users (user_id, username, password_hash, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, username, password_hash, int(is_admin), now),
        )

    return {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin,
        "created_at": now,
    }


def get_api_user_by_username(username: str) -> dict[str, Any] | None:
    """Get an API user by username."""
    db_path = get_master_db_path()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM api_users WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None


def get_api_user_by_id(user_id: str) -> dict[str, Any] | None:
    """Get an API user by ID."""
    db_path = get_master_db_path()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM api_users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
    return None
