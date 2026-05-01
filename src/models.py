from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class QueuedMediaItem:
    telegram_file_id: str
    media_kind: str
    original_file_name: str
    temp_path: Path
    sha256_hash: str
    user_id: int
    username: str
    chat_id: int
    message_id: int
    date_received: datetime = field(default_factory=datetime.utcnow)
    category: Optional[str] = None
    folder_path: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UserSession:
    active_server_id: Optional[str] = None
    current_item: Optional[QueuedMediaItem] = None
    stage: Optional[str] = None
    batch_total: int = 0
    batch_processed: int = 0
    recent_destinations: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)


@dataclass(slots=True)
class MediaRecord:
    server_id: str
    sha256_hash: str
    original_file_name: str
    telegram_file_id: str
    saved_path: str
    category: str
    folder_path: str
    received_from_user_id: int
    received_from_username: str
    telegram_chat_id: int
    telegram_message_id: int
    date_received: str
    status: str


@dataclass(slots=True)
class DuplicateResult:
    is_duplicate: bool
    existing_path: Optional[str] = None
