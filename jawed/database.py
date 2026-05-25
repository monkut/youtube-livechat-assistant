"""Database management for channel-specific SQLite databases."""

import json
import logging
import sqlite3
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
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
                issued_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # Migrate existing DBs that predate issued_at column
        with suppress(sqlite3.OperationalError):
            cursor.execute("ALTER TABLE channel_config ADD COLUMN issued_at TEXT")

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

        # Resolved SuperChat events
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                live_chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                user_sub TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                superchat_text TEXT,
                superchat_amount_micros INTEGER,
                superchat_currency TEXT,
                parsed_json TEXT,
                candidates_json TEXT,
                selected_video_id TEXT,
                selected_at TEXT,
                resolver_version TEXT,
                PRIMARY KEY (live_chat_id, message_id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_user_ts ON events (user_sub, timestamp)
        """)

        # Stream polling state
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stream_state (
                live_chat_id TEXT PRIMARY KEY,
                user_sub TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                next_page_token TEXT,
                last_poll_at TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                UNIQUE (user_sub, stream_id)
            )
        """)

        # Snapshot tombstone for stream-end JSONL export
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                user_sub TEXT NOT NULL,
                stream_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                PRIMARY KEY (user_sub, stream_id, started_at)
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
    issued_at: str | None = None,
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
                    issued_at = COALESCE(?, issued_at),
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
                    issued_at,
                    now,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO channel_config (
                    id, channel_id, channel_name, live_chat_id,
                    accepting_requests_start_datetime, accepting_requests_end_datetime,
                    access_token, refresh_token, token_expiry, issued_at,
                    created_at, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    issued_at,
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


# Events


def insert_event(
    channel_id: str,
    live_chat_id: str,
    message_id: str,
    user_sub: str,
    stream_id: str,
    timestamp: str,
    status: str,
    superchat_text: str | None = None,
    superchat_amount_micros: int | None = None,
    superchat_currency: str | None = None,
    parsed_json: str | None = None,
    candidates_json: str | None = None,
    selected_video_id: str | None = None,
    selected_at: str | None = None,
    resolver_version: str | None = None,
) -> bool:
    """Insert a SuperChat event. Returns True if inserted, False if skipped (duplicate)."""
    db_path = get_channel_db_path(channel_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO events (
                live_chat_id, message_id, user_sub, stream_id, timestamp, status,
                superchat_text, superchat_amount_micros, superchat_currency,
                parsed_json, candidates_json, selected_video_id, selected_at, resolver_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                live_chat_id, message_id, user_sub, stream_id, timestamp, status,
                superchat_text, superchat_amount_micros, superchat_currency,
                parsed_json, candidates_json, selected_video_id, selected_at, resolver_version,
            ),
        )
        return cursor.rowcount > 0


def get_event(channel_id: str, live_chat_id: str, message_id: str) -> dict[str, Any] | None:
    """Get a specific event by live_chat_id and message_id."""
    db_path = get_channel_db_path(channel_id)
    if not db_path.exists():
        return None
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM events WHERE live_chat_id = ? AND message_id = ?",
            (live_chat_id, message_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_events_for_stream(channel_id: str, live_chat_id: str) -> list[dict[str, Any]]:
    """Get all events for a stream, ordered by timestamp."""
    db_path = get_channel_db_path(channel_id)
    if not db_path.exists():
        return []
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM events WHERE live_chat_id = ? ORDER BY timestamp ASC",
            (live_chat_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


# Stream state


def upsert_stream_state(
    channel_id: str,
    live_chat_id: str,
    user_sub: str,
    stream_id: str,
    started_at: str,
    next_page_token: str | None = None,
    last_poll_at: str | None = None,
) -> dict[str, Any] | None:
    """Create or update stream polling state."""
    db_path = get_channel_db_path(channel_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO stream_state (live_chat_id, user_sub, stream_id, started_at, next_page_token, last_poll_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(live_chat_id) DO UPDATE SET
                next_page_token = COALESCE(excluded.next_page_token, next_page_token),
                last_poll_at = COALESCE(excluded.last_poll_at, last_poll_at)
            """,
            (live_chat_id, user_sub, stream_id, started_at, next_page_token, last_poll_at),
        )
    return get_stream_state(channel_id, live_chat_id)


def get_stream_state(channel_id: str, live_chat_id: str) -> dict[str, Any] | None:
    """Get stream state by live_chat_id."""
    db_path = get_channel_db_path(channel_id)
    if not db_path.exists():
        return None
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM stream_state WHERE live_chat_id = ?", (live_chat_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def end_stream(channel_id: str, live_chat_id: str) -> dict[str, Any] | None:
    """Mark a stream as ended by setting ended_at."""
    now = datetime.now(UTC).isoformat()
    db_path = get_channel_db_path(channel_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE stream_state SET ended_at = ? WHERE live_chat_id = ? AND ended_at IS NULL",
            (now, live_chat_id),
        )
    return get_stream_state(channel_id, live_chat_id)


# Snapshots


def insert_snapshot(channel_id: str, user_sub: str, stream_id: str, started_at: str) -> dict[str, Any]:
    """Insert a snapshot tombstone record at stream start."""
    db_path = get_channel_db_path(channel_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO snapshots (user_sub, stream_id, started_at) VALUES (?, ?, ?)",
            (user_sub, stream_id, started_at),
        )
    return {"user_sub": user_sub, "stream_id": stream_id, "started_at": started_at, "completed_at": None}


def complete_snapshot(channel_id: str, user_sub: str, stream_id: str, started_at: str) -> dict[str, Any] | None:
    """Mark a snapshot as completed by setting completed_at."""
    now = datetime.now(UTC).isoformat()
    db_path = get_channel_db_path(channel_id)
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE snapshots SET completed_at = ? WHERE user_sub = ? AND stream_id = ? AND started_at = ?",
            (now, user_sub, stream_id, started_at),
        )
        cursor.execute(
            "SELECT * FROM snapshots WHERE user_sub = ? AND stream_id = ? AND started_at = ?",
            (user_sub, stream_id, started_at),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_stream_snapshot(channel_id: str, live_chat_id: str) -> Path | None:
    """Write a JSONL export of all events for a stream and mark the snapshot complete.

    Triggered after stream_state.ended_at is set. Writes to {DATA_DIR}/snapshots/{live_chat_id}.jsonl.
    """
    state = get_stream_state(channel_id, live_chat_id)
    if not state:
        logger.warning("No stream state found for live_chat_id=%s", live_chat_id)
        return None

    events = get_events_for_stream(channel_id, live_chat_id)

    snapshots_dir = get_data_dir() / "snapshots"
    snapshots_dir.mkdir(exist_ok=True)
    snapshot_path = snapshots_dir / f"{live_chat_id}.jsonl"

    with snapshot_path.open("w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    complete_snapshot(channel_id, state["user_sub"], state["stream_id"], state["started_at"])
    logger.info("Snapshot created for stream %s with %d events at %s", live_chat_id, len(events), snapshot_path)
    return snapshot_path


# Token utilities (operating on channel_config — Q3 Option A)


def is_token_expired(channel_id: str) -> bool:
    """Return True if the channel's OAuth token is expired or missing."""
    config = get_channel_config(channel_id)
    if not config or not config.get("token_expiry"):
        return True
    expiry = datetime.fromisoformat(config["token_expiry"])
    return datetime.now(UTC) >= expiry


def token_near_expiry(channel_id: str, days: int = 7) -> bool:
    """Return True if the channel's OAuth token expires within `days` days."""
    config = get_channel_config(channel_id)
    if not config or not config.get("token_expiry"):
        return True
    expiry = datetime.fromisoformat(config["token_expiry"])
    return datetime.now(UTC) >= expiry - timedelta(days=days)
