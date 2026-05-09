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
    batch_destination_category: Optional[str] = None
    batch_destination_folder_path: tuple[str, ...] = field(default_factory=tuple)
    batch_destination_remaining: int = 0
    announce_new_folder_on_save: bool = False


@dataclass(slots=True)
class MediaRecord:
    server_id: str
    intake_source: str
    source_path: str
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


@dataclass(slots=True)
class PendingReviewItem:
    intake_source: str
    source_path: str
    source_modified_at: float
    source_root: str
    source_relative_path: str
    source_label: str
    original_file_name: str
    sha256_hash: str
    file_size: int
    mime_type: str
    status: str
    preview_type: str = "file_icon"
    preview_status: str = "unsupported"
    preview_error: str = ""
    thumbnail_path: str = ""
    duplicate_saved_path: str = ""
    category: str = ""
    folder_path: str = ""
    saved_path: str = ""
    reviewed_by_user_id: Optional[int] = None
    batch_token: str = ""
    received_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    notified_at: Optional[str] = None
    last_action_at: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    page_count: Optional[int] = None
    error_message: str = ""


@dataclass(slots=True)
class ReviewBatch:
    batch_token: str
    status: str
    created_at: str
    item_count: int
    last_notified_at: Optional[str] = None
