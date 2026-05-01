from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from src.models import MediaRecord


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
                CREATE INDEX IF NOT EXISTS idx_media_records_hash
                ON media_records (sha256_hash)
                """
            )
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(media_records)").fetchall()
            }
            if "folder_path" not in columns:
                self._connection.execute(
                    "ALTER TABLE media_records ADD COLUMN folder_path TEXT NOT NULL DEFAULT ''"
                )
            self._connection.commit()

    def insert_record(self, record: MediaRecord) -> None:
        path_parts = [part for part in record.folder_path.split("/") if part]
        legacy_product = path_parts[0] if path_parts else ""
        legacy_subfolder = "/".join(path_parts[1:]) if len(path_parts) > 1 else ""

        with self._lock:
            self._connection.execute(
                """
                INSERT INTO media_records (
                    server_id,
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.server_id,
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

    def close(self) -> None:
        with self._lock:
            self._connection.close()
