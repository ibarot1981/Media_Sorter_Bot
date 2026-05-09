from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.models import MediaRecord, PendingReviewItem, ReviewBatch


class Database:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    intake_source TEXT NOT NULL DEFAULT 'telegram',
                    source_path TEXT NOT NULL DEFAULT '',
                    sha256_hash TEXT NOT NULL,
                    original_file_name TEXT NOT NULL,
                    telegram_file_id TEXT NOT NULL,
                    saved_path TEXT NOT NULL,
                    category TEXT NOT NULL,
                    product TEXT NOT NULL,
                    subfolder TEXT NOT NULL,
                    folder_path TEXT NOT NULL DEFAULT '',
                    received_from_user_id INTEGER NOT NULL,
                    received_from_username TEXT NOT NULL,
                    telegram_chat_id INTEGER NOT NULL,
                    telegram_message_id INTEGER NOT NULL,
                    date_received TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intake_source TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_modified_at REAL NOT NULL,
                    source_root TEXT NOT NULL,
                    source_relative_path TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    original_file_name TEXT NOT NULL,
                    sha256_hash TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    mime_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    preview_type TEXT NOT NULL DEFAULT 'file_icon',
                    preview_status TEXT NOT NULL DEFAULT 'unsupported',
                    preview_error TEXT NOT NULL DEFAULT '',
                    thumbnail_path TEXT NOT NULL DEFAULT '',
                    duplicate_saved_path TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    folder_path TEXT NOT NULL DEFAULT '',
                    saved_path TEXT NOT NULL DEFAULT '',
                    reviewed_by_user_id INTEGER,
                    batch_token TEXT NOT NULL DEFAULT '',
                    received_at TEXT NOT NULL,
                    notified_at TEXT,
                    last_action_at TEXT,
                    width INTEGER,
                    height INTEGER,
                    duration_seconds REAL,
                    page_count INTEGER,
                    error_message TEXT NOT NULL DEFAULT ''
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_batches (
                    batch_token TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL,
                    last_notified_at TEXT
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_batch_items (
                    batch_token TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    PRIMARY KEY (batch_token, item_id),
                    FOREIGN KEY (batch_token) REFERENCES review_batches(batch_token) ON DELETE CASCADE,
                    FOREIGN KEY (item_id) REFERENCES pending_items(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS review_favorite_destinations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    folder_path TEXT NOT NULL DEFAULT '',
                    label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    UNIQUE (category, folder_path)
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_records_hash
                ON media_records (sha256_hash)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_items_status_received
                ON pending_items (status, received_at)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_items_hash
                ON pending_items (sha256_hash)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_items_source_path
                ON pending_items (source_path)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_favorite_destinations_last_used
                ON review_favorite_destinations (COALESCE(last_used_at, created_at))
                """
            )
            self._ensure_media_record_columns()
            self._ensure_pending_item_columns()
            self._connection.commit()

    def _ensure_media_record_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(media_records)").fetchall()
        }
        if "folder_path" not in columns:
            self._connection.execute(
                "ALTER TABLE media_records ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''"
            )
        if "intake_source" not in columns:
            self._connection.execute(
                "ALTER TABLE media_records ADD COLUMN intake_source TEXT NOT NULL DEFAULT 'telegram'"
            )
        if "source_path" not in columns:
            self._connection.execute(
                "ALTER TABLE media_records ADD COLUMN source_path TEXT NOT NULL DEFAULT ''"
            )

    def _ensure_pending_item_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute("PRAGMA table_info(pending_items)").fetchall()
        }
        if "source_modified_at" not in columns:
            self._connection.execute(
                "ALTER TABLE pending_items ADD COLUMN source_modified_at REAL NOT NULL DEFAULT 0"
            )

    def insert_record(self, record: MediaRecord) -> None:
        path_parts = [part for part in record.folder_path.split("/") if part]
        legacy_product = path_parts[0] if path_parts else ""
        legacy_subfolder = "/".join(path_parts[1:]) if len(path_parts) > 1 else ""

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO media_records (
                    server_id,
                    intake_source,
                    source_path,
                    sha256_hash,
                    original_file_name,
                    telegram_file_id,
                    saved_path,
                    category,
                    product,
                    subfolder,
                    folder_path,
                    received_from_user_id,
                    received_from_username,
                    telegram_chat_id,
                    telegram_message_id,
                    date_received,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.server_id,
                    record.intake_source,
                    record.source_path,
                    record.sha256_hash,
                    record.original_file_name,
                    record.telegram_file_id,
                    record.saved_path,
                    record.category,
                    legacy_product,
                    legacy_subfolder,
                    record.folder_path,
                    record.received_from_user_id,
                    record.received_from_username,
                    record.telegram_chat_id,
                    record.telegram_message_id,
                    record.date_received,
                    record.status,
                ),
            )
            self._connection.commit()

    def find_by_hash(self, sha256_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT *
                FROM media_records
                WHERE sha256_hash = ?
                  AND status = 'saved'
                ORDER BY id ASC
                LIMIT 1
                """,
                (sha256_hash,),
            ).fetchone()
        return dict(row) if row else None

    def find_pending_by_source_path(self, source_path: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT *
                FROM pending_items
                WHERE source_path = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_path,),
            ).fetchone()
        return dict(row) if row else None

    def get_pending_item(self, item_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM pending_items WHERE id = ? LIMIT 1",
                (item_id,),
            ).fetchone()
        return dict(row) if row else None

    def insert_pending_item(self, item: PendingReviewItem) -> int:
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO pending_items (
                    intake_source,
                    source_path,
                    source_modified_at,
                    source_root,
                    source_relative_path,
                    source_label,
                    original_file_name,
                    sha256_hash,
                    file_size,
                    mime_type,
                    status,
                    preview_type,
                    preview_status,
                    preview_error,
                    thumbnail_path,
                    duplicate_saved_path,
                    category,
                    folder_path,
                    saved_path,
                    reviewed_by_user_id,
                    batch_token,
                    received_at,
                    notified_at,
                    last_action_at,
                    width,
                    height,
                    duration_seconds,
                    page_count,
                    error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.intake_source,
                    item.source_path,
                    item.source_modified_at,
                    item.source_root,
                    item.source_relative_path,
                    item.source_label,
                    item.original_file_name,
                    item.sha256_hash,
                    item.file_size,
                    item.mime_type,
                    item.status,
                    item.preview_type,
                    item.preview_status,
                    item.preview_error,
                    item.thumbnail_path,
                    item.duplicate_saved_path,
                    item.category,
                    item.folder_path,
                    item.saved_path,
                    item.reviewed_by_user_id,
                    item.batch_token,
                    item.received_at,
                    item.notified_at,
                    item.last_action_at,
                    item.width,
                    item.height,
                    item.duration_seconds,
                    item.page_count,
                    item.error_message,
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def update_pending_preview(
        self,
        item_id: int,
        *,
        thumbnail_path: str,
        preview_type: str,
        preview_status: str,
        preview_error: str = "",
        width: int | None = None,
        height: int | None = None,
        duration_seconds: float | None = None,
        page_count: int | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE pending_items
                SET thumbnail_path = ?,
                    preview_type = ?,
                    preview_status = ?,
                    preview_error = ?,
                    width = ?,
                    height = ?,
                    duration_seconds = ?,
                    page_count = ?
                WHERE id = ?
                """,
                (
                    thumbnail_path,
                    preview_type,
                    preview_status,
                    preview_error,
                    width,
                    height,
                    duration_seconds,
                    page_count,
                    item_id,
                ),
            )
            self._connection.commit()

    def update_pending_status(
        self,
        item_id: int,
        *,
        status: str,
        category: str | None = None,
        folder_path: str | None = None,
        saved_path: str | None = None,
        reviewed_by_user_id: int | None = None,
        error_message: str | None = None,
        duplicate_saved_path: str | None = None,
        batch_token: str | None = None,
    ) -> None:
        assignments = ["status = ?", "last_action_at = ?"]
        params: list[Any] = [status, datetime.utcnow().isoformat()]

        if category is not None:
            assignments.append("category = ?")
            params.append(category)
        if folder_path is not None:
            assignments.append("folder_path = ?")
            params.append(folder_path)
        if saved_path is not None:
            assignments.append("saved_path = ?")
            params.append(saved_path)
        if reviewed_by_user_id is not None:
            assignments.append("reviewed_by_user_id = ?")
            params.append(reviewed_by_user_id)
        if error_message is not None:
            assignments.append("error_message = ?")
            params.append(error_message)
        if duplicate_saved_path is not None:
            assignments.append("duplicate_saved_path = ?")
            params.append(duplicate_saved_path)
        if batch_token is not None:
            assignments.append("batch_token = ?")
            params.append(batch_token)

        params.append(item_id)
        query = f"UPDATE pending_items SET {', '.join(assignments)} WHERE id = ?"
        with self._lock:
            self._connection.execute(query, params)
            self._connection.commit()

    def list_pending_items(
        self,
        *,
        statuses: Iterable[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM pending_items"
        params: list[Any] = []
        if statuses:
            status_list = list(statuses)
            placeholders = ", ".join("?" for _ in status_list)
            query += f" WHERE status IN ({placeholders})"
            params.extend(status_list)
        query += " ORDER BY received_at ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_items_ready_for_notification(self, *, cutoff_iso: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM pending_items
                WHERE status = 'pending_review'
                  AND (batch_token = '' OR batch_token IS NULL)
                  AND received_at <= ?
                ORDER BY received_at ASC
                LIMIT ?
                """,
                (cutoff_iso, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_review_batch(self, batch: ReviewBatch, item_ids: list[int]) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO review_batches (
                    batch_token,
                    status,
                    created_at,
                    item_count,
                    last_notified_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch.batch_token,
                    batch.status,
                    batch.created_at,
                    batch.item_count,
                    batch.last_notified_at,
                ),
            )
            for item_id in item_ids:
                self._connection.execute(
                    """
                    INSERT INTO review_batch_items (batch_token, item_id)
                    VALUES (?, ?)
                    """,
                    (batch.batch_token, item_id),
                )
                self._connection.execute(
                    """
                    UPDATE pending_items
                    SET status = 'notified',
                        batch_token = ?,
                        notified_at = ?,
                        last_action_at = ?
                    WHERE id = ?
                    """,
                    (batch.batch_token, batch.last_notified_at, batch.last_notified_at, item_id),
                )
            self._connection.commit()

    def get_review_batch(self, batch_token: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM review_batches WHERE batch_token = ? LIMIT 1",
                (batch_token,),
            ).fetchone()
        return dict(row) if row else None

    def list_review_batches(
        self,
        *,
        statuses: Iterable[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM review_batches"
        params: list[Any] = []
        if statuses:
            status_list = list(statuses)
            placeholders = ", ".join("?" for _ in status_list)
            query += f" WHERE status IN ({placeholders})"
            params.extend(status_list)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_recent_destinations(self, *, limit: int = 8) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT
                    category,
                    folder_path,
                    MAX(id) AS latest_id,
                    MAX(date_received) AS last_used_at,
                    COUNT(*) AS use_count
                FROM media_records
                WHERE status = 'saved'
                  AND category <> ''
                GROUP BY category, folder_path
                ORDER BY latest_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_favorite_destinations(self, *, limit: int = 12) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT *
                FROM review_favorite_destinations
                ORDER BY COALESCE(last_used_at, created_at) DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_favorite_destination(
        self,
        *,
        category: str,
        folder_path: str,
        label: str,
    ) -> None:
        now = datetime.utcnow().isoformat()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO review_favorite_destinations (
                    category,
                    folder_path,
                    label,
                    created_at,
                    last_used_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(category, folder_path) DO UPDATE SET
                    label = excluded.label,
                    last_used_at = excluded.last_used_at
                """,
                (category, folder_path, label, now, now),
            )
            self._connection.commit()

    def delete_favorite_destination(self, *, category: str, folder_path: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                DELETE FROM review_favorite_destinations
                WHERE category = ?
                  AND folder_path = ?
                """,
                (category, folder_path),
            )
            self._connection.commit()

    def touch_favorite_destination(self, *, category: str, folder_path: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE review_favorite_destinations
                SET last_used_at = ?
                WHERE category = ?
                  AND folder_path = ?
                """,
                (datetime.utcnow().isoformat(), category, folder_path),
            )
            self._connection.commit()

    def list_batches_for_resend(
        self,
        *,
        cutoff_iso: str,
        statuses: Iterable[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        status_list = list(statuses or ["open", "review_in_progress"])
        placeholders = ", ".join("?" for _ in status_list)
        params: list[Any] = [*status_list, cutoff_iso, limit]
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT *
                FROM review_batches
                WHERE status IN ({placeholders})
                  AND COALESCE(last_notified_at, created_at) <= ?
                ORDER BY COALESCE(last_notified_at, created_at) ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_batch_items(self, batch_token: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT pending_items.*
                FROM pending_items
                INNER JOIN review_batch_items
                    ON review_batch_items.item_id = pending_items.id
                WHERE review_batch_items.batch_token = ?
                ORDER BY pending_items.received_at ASC
                """,
                (batch_token,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_batch_status(self, batch_token: str, status: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE review_batches
                SET status = ?,
                    last_notified_at = COALESCE(last_notified_at, ?)
                WHERE batch_token = ?
                """,
                (status, datetime.utcnow().isoformat(), batch_token),
            )
            self._connection.commit()

    def touch_batch_notification(self, batch_token: str, notified_at: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE review_batches
                SET last_notified_at = ?
                WHERE batch_token = ?
                """,
                (notified_at, batch_token),
            )
            self._connection.commit()

    def get_last_batch_created_at(self) -> str | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT created_at
                FROM review_batches
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        return str(row["created_at"]) if row else None

    def close(self) -> None:
        with self._lock:
            self._connection.close()
