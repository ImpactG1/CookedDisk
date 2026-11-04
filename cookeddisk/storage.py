"""SQLite-backed storage for sessions, segments, and upload state."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cookeddisk.config import get_default_app_dir
from cookeddisk.logger import get_logger

logger = get_logger("storage")


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionRecord:
    session_id: str
    name: str
    drive_folder_id: Optional[str]
    started_at: str
    ended_at: Optional[str]
    status: str
    total_segments: int
    uploaded_segments: int
    total_bytes: int
    uploaded_bytes: int


@dataclass
class SegmentRecord:
    id: int
    session_id: str
    file_path: str
    file_name: str
    file_size: int
    local_md5: Optional[str]
    drive_file_id: Optional[str]
    drive_md5: Optional[str]
    status: str  # DETECTED, READY, UPLOADING, UPLOADED, VERIFYING, VERIFIED, DELETED, FAILED
    attempts: int
    last_error: Optional[str]
    resumable_uri: Optional[str]
    bytes_uploaded: int
    created_at: str
    updated_at: str


class Storage:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or (get_default_app_dir() / "cookeddisk.db")
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    drive_folder_id TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    total_segments INTEGER NOT NULL DEFAULT 0,
                    uploaded_segments INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    uploaded_bytes INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    file_path TEXT UNIQUE NOT NULL,
                    file_name TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    local_md5 TEXT,
                    drive_file_id TEXT,
                    drive_md5 TEXT,
                    status TEXT NOT NULL DEFAULT 'DETECTED',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    resumable_uri TEXT,
                    bytes_uploaded INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_segments_status ON segments(status);
                CREATE INDEX IF NOT EXISTS idx_segments_session ON segments(session_id);
                CREATE INDEX IF NOT EXISTS idx_segments_created ON segments(created_at);
                """
            )
            conn.commit()

    # ------------------ Session Operations ------------------ #

    def get_or_create_session(self, session_id: str, name: Optional[str] = None) -> SessionRecord:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            if row:
                return SessionRecord(**dict(row))

            now = utc_iso_now()
            display_name = name or session_id
            conn.execute(
                """
                INSERT INTO sessions (session_id, name, started_at, status)
                VALUES (?, ?, ?, 'ACTIVE')
                """,
                (session_id, display_name, now),
            )
            conn.commit()
            return SessionRecord(
                session_id=session_id,
                name=display_name,
                drive_folder_id=None,
                started_at=now,
                ended_at=None,
                status="ACTIVE",
                total_segments=0,
                uploaded_segments=0,
                total_bytes=0,
                uploaded_bytes=0,
            )

    def set_session_drive_folder(self, session_id: str, drive_folder_id: str) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET drive_folder_id = ? WHERE session_id = ?",
                (drive_folder_id, session_id),
            )
            conn.commit()

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
            return SessionRecord(**dict(row)) if row else None

    def list_sessions(self, limit: int = 50) -> List[SessionRecord]:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            )
            return [SessionRecord(**dict(row)) for row in cur.fetchall()]

    # ------------------ Segment Operations ------------------ #

    def add_or_update_detected_segment(
        self, session_id: str, file_path: str, file_name: str, file_size: int
    ) -> SegmentRecord:
        with self._lock, self._get_connection() as conn:
            self.get_or_create_session(session_id)
            now = utc_iso_now()
            cur = conn.execute("SELECT * FROM segments WHERE file_path = ?", (file_path,))
            row = cur.fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE segments
                    SET file_size = ?, updated_at = ?
                    WHERE file_path = ?
                    """,
                    (file_size, now, file_path),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO segments (
                        session_id, file_path, file_name, file_size, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'DETECTED', ?, ?)
                    """,
                    (session_id, file_path, file_name, file_size, now, now),
                )
                conn.execute(
                    """
                    UPDATE sessions
                    SET total_segments = total_segments + 1,
                        total_bytes = total_bytes + ?
                    WHERE session_id = ?
                    """,
                    (file_size, session_id),
                )
            conn.commit()

            cur = conn.execute("SELECT * FROM segments WHERE file_path = ?", (file_path,))
            return SegmentRecord(**dict(cur.fetchone()))

    def mark_segment_ready(self, file_path: str, file_size: int) -> None:
        with self._lock, self._get_connection() as conn:
            now = utc_iso_now()
            conn.execute(
                """
                UPDATE segments
                SET status = 'READY', file_size = ?, updated_at = ?
                WHERE file_path = ? AND status IN ('DETECTED', 'FAILED')
                """,
                (file_size, now, file_path),
            )
            conn.commit()

    def get_oldest_ready_segment(self) -> Optional[SegmentRecord]:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM segments
                WHERE status = 'READY'
                ORDER BY created_at ASC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            return SegmentRecord(**dict(row)) if row else None

    def update_segment_uploading(
        self, segment_id: int, resumable_uri: Optional[str] = None
    ) -> None:
        with self._lock, self._get_connection() as conn:
            now = utc_iso_now()
            conn.execute(
                """
                UPDATE segments
                SET status = 'UPLOADING',
                    attempts = attempts + 1,
                    resumable_uri = COALESCE(?, resumable_uri),
                    updated_at = ?
                WHERE id = ?
                """,
                (resumable_uri, now, segment_id),
            )
            conn.commit()

    def update_segment_progress(self, segment_id: int, bytes_uploaded: int) -> None:
        with self._lock, self._get_connection() as conn:
            now = utc_iso_now()
            conn.execute(
                """
                UPDATE segments
                SET bytes_uploaded = ?, updated_at = ?
                WHERE id = ?
                """,
                (bytes_uploaded, now, segment_id),
            )
            conn.commit()

    def mark_segment_uploaded(
        self,
        segment_id: int,
        drive_file_id: str,
        drive_md5: Optional[str],
        local_md5: Optional[str],
    ) -> None:
        with self._lock, self._get_connection() as conn:
            now = utc_iso_now()
            conn.execute(
                """
                UPDATE segments
                SET status = 'UPLOADED',
                    drive_file_id = ?,
                    drive_md5 = ?,
                    local_md5 = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (drive_file_id, drive_md5, local_md5, now, segment_id),
            )
            conn.commit()

    def mark_segment_verified_and_deleted(self, segment_id: int) -> None:
        with self._lock, self._get_connection() as conn:
            now = utc_iso_now()
            cur = conn.execute(
                "SELECT session_id, file_size FROM segments WHERE id = ?", (segment_id,)
            )
            row = cur.fetchone()
            if not row:
                return

            session_id = row["session_id"]
            file_size = row["file_size"]

            conn.execute(
                """
                UPDATE segments
                SET status = 'VERIFIED',
                    bytes_uploaded = file_size,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, segment_id),
            )
            conn.execute(
                """
                UPDATE sessions
                SET uploaded_segments = uploaded_segments + 1,
                    uploaded_bytes = uploaded_bytes + ?
                WHERE session_id = ?
                """,
                (file_size, session_id),
            )
            conn.commit()

    def mark_segment_failed(self, segment_id: int, error_msg: str) -> None:
        with self._lock, self._get_connection() as conn:
            now = utc_iso_now()
            conn.execute(
                """
                UPDATE segments
                SET status = 'FAILED',
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_msg, now, segment_id),
            )
            conn.commit()

    def get_queue_stats(self) -> Dict[str, Any]:
        """Returns statistics on pending segments that consume local disk."""
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT
                    COUNT(*) as pending_count,
                    COALESCE(SUM(file_size), 0) as pending_bytes
                FROM segments
                WHERE status IN ('DETECTED', 'READY', 'UPLOADING', 'UPLOADED', 'VERIFYING', 'FAILED')
                """
            )
            row = cur.fetchone()
            pending_count = row["pending_count"] if row else 0
            pending_bytes = row["pending_bytes"] if row else 0

            cur_act = conn.execute(
                "SELECT COUNT(*) as active_count FROM segments WHERE status = 'UPLOADING'"
            )
            active_count = cur_act.fetchone()["active_count"]

            return {
                "pending_count": pending_count,
                "pending_bytes": pending_bytes,
                "pending_mb": pending_bytes / (1024 * 1024),
                "is_uploading": active_count > 0,
            }

    def get_recent_segments(self, limit: int = 50) -> List[SegmentRecord]:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM segments
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [SegmentRecord(**dict(row)) for row in cur.fetchall()]

    def get_segments_for_session(self, session_id: str) -> List[SegmentRecord]:
        with self._lock, self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM segments
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            )
            return [SegmentRecord(**dict(row)) for row in cur.fetchall()]

    def reset_in_progress_segments(self) -> int:
        """Crash recovery: reset any segments stuck in UPLOADING or VERIFYING to READY."""
        with self._lock, self._get_connection() as conn:
            now = utc_iso_now()
            cur = conn.execute(
                """
                UPDATE segments
                SET status = 'READY',
                    updated_at = ?
                WHERE status IN ('UPLOADING', 'VERIFYING')
                """,
                (now,),
            )
            conn.commit()
            count = cur.rowcount
            if count > 0:
                logger.info(f"Crash recovery: reset {count} interrupted segments back to READY.")
            return count

