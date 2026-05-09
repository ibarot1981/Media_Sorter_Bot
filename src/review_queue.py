from __future__ import annotations

import json
import logging
import mimetypes
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import parse, request
from uuid import uuid4

from src.config import AppConfig, load_config, save_config
from src.database import Database
from src.duplicates import DuplicateChecker
from src.models import MediaRecord, PendingReviewItem, ReviewBatch
from src.storage import StorageService
from src.utils import compute_sha256

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency at runtime
    Image = None

try:
    import fitz
except ImportError:  # pragma: no cover - optional dependency at runtime
    fitz = None

try:
    import imageio_ffmpeg
except ImportError:  # pragma: no cover - optional dependency at runtime
    imageio_ffmpeg = None


PENDING_REVIEW_STATUSES = {"pending_review", "notified", "review_in_progress"}
OPEN_BATCH_STATUSES = {"open", "review_in_progress"}
IGNORED_FILE_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
IGNORED_PATH_PARTS = {".stfolder", ".stignore", ".stversions"}


class ReviewQueueService:
    def __init__(
        self,
        config: AppConfig,
        config_path: Path,
        database: Database,
        storage: StorageService,
        duplicate_checker: DuplicateChecker,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.database = database
        self.storage = storage
        self.duplicate_checker = duplicate_checker
        self.logger = logging.getLogger(__name__)

    def refresh_runtime_config(self) -> AppConfig:
        latest_config = load_config(self.config_path)
        self.config = latest_config
        self.storage.refresh_config(latest_config)
        self.duplicate_checker.duplicate_action = latest_config.behavior.duplicate_action
        return latest_config

    def run_forever(self) -> None:
        self.logger.info("Starting Syncthing review watcher loop")
        while True:
            try:
                self.refresh_runtime_config()
                if self.config.review_queue.enabled:
                    self.scan_inbox_once()
                    self.maybe_send_batched_notification()
            except Exception as exc:  # pragma: no cover - defensive loop protection
                self.logger.exception("Review queue loop failed: %s", exc)
            time.sleep(max(5, self.config.review_queue.poll_interval_seconds))

    def scan_inbox_once(self) -> int:
        inbox_root = self._get_inbox_root()
        if not inbox_root:
            return 0

        discovered = 0
        for source_path in sorted(inbox_root.rglob("*")):
            if not source_path.is_file():
                continue
            if self._should_ignore_path(source_path, inbox_root):
                continue
            if not self._is_file_stable(source_path):
                continue
            try:
                if self._ingest_source_file(source_path):
                    discovered += 1
            except Exception as exc:
                self.logger.exception("Failed to ingest %s: %s", source_path, exc)
        return discovered

    def maybe_send_batched_notification(self) -> str | None:
        last_batch_created_at = self.database.get_last_batch_created_at()
        if last_batch_created_at:
            last_batch_dt = datetime.fromisoformat(last_batch_created_at)
            if datetime.utcnow() - last_batch_dt < timedelta(minutes=self.config.review_queue.notification_batch_minutes):
                return None

        cutoff = datetime.utcnow() - timedelta(minutes=self.config.review_queue.notification_batch_minutes)
        ready_items = self.database.list_items_ready_for_notification(
            cutoff_iso=cutoff.isoformat(),
            limit=self.config.review_queue.batch_size_default,
        )
        if not ready_items:
            return None

        batch_token = uuid4().hex
        review_url = self._build_review_url(batch_token)
        summary_text = self._build_notification_text(ready_items, review_url)
        delivered = False
        for user_id in self.config.security.allowed_telegram_user_ids:
            delivered = self._send_telegram_message(
                user_id,
                summary_text,
                button_text="Open Review",
                button_url=review_url,
            ) or delivered

        if not delivered:
            self.logger.warning("Review batch was prepared but no Telegram notification could be delivered.")
            return None

        timestamp = datetime.utcnow().isoformat()
        self.database.create_review_batch(
            ReviewBatch(
                batch_token=batch_token,
                status="open",
                created_at=timestamp,
                item_count=len(ready_items),
                last_notified_at=timestamp,
            ),
            [int(item["id"]) for item in ready_items],
        )
        self.logger.info("Delivered Telegram review batch %s with %s item(s)", batch_token, len(ready_items))
        return batch_token

    def maybe_resend_open_batches(self) -> str | None:
        cutoff = datetime.utcnow() - timedelta(minutes=self.config.review_queue.notification_batch_minutes)
        batches = self.database.list_batches_for_resend(cutoff_iso=cutoff.isoformat(), limit=1)
        if not batches:
            return None

        batch = batches[0]
        batch_token = str(batch["batch_token"])
        items = self.database.list_batch_items(batch_token)
        pending_items = [item for item in items if str(item["status"]) in PENDING_REVIEW_STATUSES]
        if not pending_items:
            self.database.mark_batch_status(batch_token, "completed")
            return None

        review_url = self._build_review_url(batch_token)
        summary_text = self._build_notification_text(pending_items, review_url, is_reminder=True)
        delivered = False
        for user_id in self.config.security.allowed_telegram_user_ids:
            delivered = self._send_telegram_message(
                user_id,
                summary_text,
                button_text="Open Review",
                button_url=review_url,
            ) or delivered

        if not delivered:
            return None

        notified_at = datetime.utcnow().isoformat()
        self.database.touch_batch_notification(batch_token, notified_at)
        self.logger.info("Re-sent Telegram review batch %s with %s pending item(s)", batch_token, len(pending_items))
        return batch_token

    def list_dashboard_items(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self.refresh_runtime_config()
        items = self.database.list_pending_items(statuses=PENDING_REVIEW_STATUSES, limit=limit)
        return [self._with_preview_context(item) for item in items]

    def build_review_dashboard_url(self) -> str:
        base_url = self.config.review_queue.review_base_url.strip()
        if not base_url:
            base_url = f"http://{self.config.webui.host}:{self.config.webui.port}"
        return f"{base_url.rstrip('/')}/review"

    def build_batch_review_url(self, batch_token: str) -> str:
        return self._build_review_url(batch_token)

    def list_pending_batches(self, *, limit: int = 10) -> list[dict[str, Any]]:
        self.refresh_runtime_config()
        batches = self.database.list_review_batches(statuses=OPEN_BATCH_STATUSES, limit=limit)
        summaries: list[dict[str, Any]] = []

        for batch in batches:
            batch_token = str(batch["batch_token"])
            items = self.database.list_batch_items(batch_token)
            pending_count = sum(1 for item in items if str(item["status"]) in PENDING_REVIEW_STATUSES)
            if pending_count <= 0:
                if str(batch["status"]) != "completed":
                    self.database.mark_batch_status(batch_token, "completed")
                continue

            summaries.append(
                {
                    **batch,
                    "pending_count": pending_count,
                    "review_url": self._build_review_url(batch_token),
                }
            )

        return summaries

    def get_latest_pending_batch(self) -> dict[str, Any] | None:
        batches = self.list_pending_batches(limit=1)
        return batches[0] if batches else None

    def build_pending_batches_message(self, *, limit: int = 5) -> str:
        batches = self.list_pending_batches(limit=limit)
        if not batches:
            return (
                "No Syncthing review batches are waiting right now.\n\n"
                "New files will still send a single notification when a fresh batch is created."
            )

        total_pending = sum(int(batch["pending_count"]) for batch in batches)
        lines = [
            f"{len(batches)} pending review batch(es) are open with {total_pending} file(s) still waiting.",
            "",
        ]
        for index, batch in enumerate(batches, start=1):
            created_at = str(batch.get("created_at", "")).replace("T", " ")[:16]
            lines.append(
                f"{index}. Batch {str(batch['batch_token'])[:8]} - "
                f"{batch['pending_count']} pending - {created_at}"
            )

        if len(batches) >= limit:
            lines.extend(
                [
                    "",
                    "Open the review queue to see every pending batch.",
                ]
            )

        return "\n".join(lines)

    def get_batch_items(self, batch_token: str) -> list[dict[str, Any]]:
        self.refresh_runtime_config()
        batch = self.database.get_review_batch(batch_token)
        if not batch:
            return []
        self.database.mark_batch_status(batch_token, "review_in_progress")
        items = self.database.list_batch_items(batch_token)
        return [self._with_preview_context(item) for item in items]

    def build_destination_options(self) -> list[str]:
        self.refresh_runtime_config()
        options: list[str] = []
        for category in self.config.categories:
            options.append(category.name)
            self._collect_folder_options(category.name, [], category.folders, options)
        return options

    def list_recent_destinations(self, *, limit: int = 8) -> list[dict[str, Any]]:
        self.refresh_runtime_config()
        favorite_lookup = {
            (str(item["category"]), str(item["folder_path"] or ""))
            for item in self.database.list_favorite_destinations(limit=50)
        }
        recent_items = self.database.list_recent_destinations(limit=limit)
        return [
            self._build_destination_item(
                category=str(item["category"]),
                folder_path=self._split_folder_path(str(item["folder_path"] or "")),
                is_favorite=(str(item["category"]), str(item["folder_path"] or "")) in favorite_lookup,
                use_count=int(item.get("use_count", 0) or 0),
            )
            for item in recent_items
        ]

    def list_favorite_destinations(self, *, limit: int = 12) -> list[dict[str, Any]]:
        self.refresh_runtime_config()
        favorites = self.database.list_favorite_destinations(limit=limit)
        return [
            self._build_destination_item(
                category=str(item["category"]),
                folder_path=self._split_folder_path(str(item["folder_path"] or "")),
                is_favorite=True,
                label=str(item.get("label", "") or ""),
            )
            for item in favorites
        ]

    def save_favorite_destination(self, destination_value: str) -> dict[str, Any]:
        self.refresh_runtime_config()
        category_name, folder_path = self.parse_destination_value(destination_value)
        label = self._format_destination_label(category_name, folder_path)
        self.database.save_favorite_destination(
            category=category_name,
            folder_path="/".join(folder_path),
            label=label,
        )
        return self._build_destination_item(
            category=category_name,
            folder_path=folder_path,
            is_favorite=True,
            label=label,
        )

    def remove_favorite_destination(self, destination_value: str) -> dict[str, Any]:
        self.refresh_runtime_config()
        category_name, folder_path = self.parse_destination_value(destination_value)
        self.database.delete_favorite_destination(
            category=category_name,
            folder_path="/".join(folder_path),
        )
        return self._build_destination_item(
            category=category_name,
            folder_path=folder_path,
            is_favorite=False,
        )

    def save_items(
        self,
        item_ids: list[int],
        destination_value: str,
        *,
        reviewed_by_user_id: int = 0,
    ) -> dict[str, Any]:
        self.refresh_runtime_config()
        category_name, folder_path = self.parse_destination_value(destination_value)
        self._ensure_destination_path(category_name, folder_path)
        folder_path_str = "/".join(folder_path)

        saved_items: list[dict[str, Any]] = []
        failed_items: list[tuple[int, str]] = []
        for item_id in item_ids:
            item = self.database.get_pending_item(item_id)
            if not item or item["status"] not in PENDING_REVIEW_STATUSES:
                continue

            source_path = Path(str(item["source_path"]))
            if not source_path.exists():
                self.database.update_pending_status(
                    item_id,
                    status="error",
                    error_message="Source file was missing when trying to finalize review item.",
                    reviewed_by_user_id=reviewed_by_user_id,
                )
                failed_items.append((item_id, "Source file missing"))
                continue

            try:
                final_path = self.storage.finalize_review_item(
                    source_path=source_path,
                    original_file_name=str(item["original_file_name"]),
                    category=category_name,
                    folder_path=folder_path,
                    delete_source_after_save=self.config.review_queue.delete_inbox_file_after_save,
                )
                media_record = MediaRecord(
                    server_id=self.config.server.server_id,
                    intake_source=str(item["intake_source"]),
                    source_path=str(item["source_path"]),
                    sha256_hash=str(item["sha256_hash"]),
                    original_file_name=str(item["original_file_name"]),
                    telegram_file_id="",
                    saved_path=str(final_path),
                    category=category_name,
                    folder_path=folder_path_str,
                    received_from_user_id=reviewed_by_user_id,
                    received_from_username="syncthing_review",
                    telegram_chat_id=0,
                    telegram_message_id=0,
                    date_received=str(item["received_at"]),
                    status="saved",
                )
                self.database.insert_record(media_record)
                self.database.update_pending_status(
                    item_id,
                    status="saved",
                    category=category_name,
                    folder_path=folder_path_str,
                    saved_path=str(final_path),
                    reviewed_by_user_id=reviewed_by_user_id,
                    error_message="",
                )
                saved_items.append({"id": item_id, "saved_path": str(final_path)})
            except Exception as exc:  # pragma: no cover - filesystem failures are environment-specific
                self.logger.exception("Failed to finalize pending review item %s: %s", item_id, exc)
                self.database.update_pending_status(
                    item_id,
                    status="error",
                    error_message=str(exc),
                    reviewed_by_user_id=reviewed_by_user_id,
                )
                failed_items.append((item_id, str(exc)))

        self.database.touch_favorite_destination(category=category_name, folder_path=folder_path_str)
        self._refresh_related_batch_statuses(item_ids)
        return {
            "saved_count": len(saved_items),
            "failed_count": len(failed_items),
            "saved_items": saved_items,
            "failed_items": failed_items,
            "destination": " / ".join([category_name, *folder_path]) if folder_path else category_name,
        }

    def skip_items(self, item_ids: list[int], *, reviewed_by_user_id: int = 0) -> dict[str, Any]:
        self.refresh_runtime_config()
        skipped_count = 0
        failed_items: list[tuple[int, str]] = []
        for item_id in item_ids:
            item = self.database.get_pending_item(item_id)
            if not item or item["status"] not in PENDING_REVIEW_STATUSES:
                continue

            source_path = Path(str(item["source_path"]))
            if not source_path.exists():
                self.database.update_pending_status(
                    item_id,
                    status="error",
                    error_message="Source file was missing when trying to skip review item.",
                    reviewed_by_user_id=reviewed_by_user_id,
                )
                failed_items.append((item_id, "Source file missing"))
                continue

            try:
                archived_path = self.storage.archive_review_item(source_path, "skipped")
                self.database.update_pending_status(
                    item_id,
                    status="skipped",
                    saved_path=str(archived_path),
                    reviewed_by_user_id=reviewed_by_user_id,
                    error_message="",
                )
                skipped_count += 1
            except Exception as exc:  # pragma: no cover - filesystem failures are environment-specific
                self.logger.exception("Failed to skip pending review item %s: %s", item_id, exc)
                self.database.update_pending_status(
                    item_id,
                    status="error",
                    error_message=str(exc),
                    reviewed_by_user_id=reviewed_by_user_id,
                )
                failed_items.append((item_id, str(exc)))

        self._refresh_related_batch_statuses(item_ids)
        return {
            "skipped_count": skipped_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
        }

    def parse_destination_value(self, destination_value: str) -> tuple[str, list[str]]:
        raw_value = str(destination_value or "").strip()
        if not raw_value:
            raise ValueError("Choose a destination before saving selected files.")

        parts = [part.strip() for part in raw_value.split("/") if part.strip()]
        if not parts:
            raise ValueError("Choose a valid destination.")

        category_name = parts[0]
        folder_path = parts[1:]
        category = self.config.get_category(category_name)
        if not category:
            raise ValueError(f"Unknown category: {category_name}")
        return category_name, folder_path

    def _ensure_destination_path(self, category_name: str, folder_path: list[str]) -> None:
        latest_config = self.refresh_runtime_config()
        exists = self._folder_path_exists(category_name, folder_path)
        if exists:
            return
        if not latest_config.behavior.allow_new_folder:
            raise ValueError("This destination does not exist and new folder creation is disabled.")

        changed = latest_config.ensure_folder_path(category_name, folder_path)
        if changed:
            save_config(latest_config, self.config_path, create_backup=False)
        self.config = latest_config

    def _folder_path_exists(self, category_name: str, folder_path: list[str]) -> bool:
        category = self.config.get_category(category_name)
        if not category:
            return False

        nodes = category.folders
        for path_part in folder_path:
            match = next((node for node in nodes if node.name == path_part), None)
            if not match:
                return False
            nodes = match.folders
        return True

    def _collect_folder_options(
        self,
        category_name: str,
        prefix: list[str],
        nodes,
        options: list[str],
    ) -> None:
        for node in nodes:
            current_path = [*prefix, node.name]
            options.append(" / ".join([category_name, *current_path]))
            self._collect_folder_options(category_name, current_path, node.folders, options)

    def _refresh_related_batch_statuses(self, item_ids: list[int]) -> None:
        batch_tokens = {
            str(item["batch_token"])
            for item_id in item_ids
            if (item := self.database.get_pending_item(item_id)) and str(item["batch_token"]).strip()
        }
        for batch_token in batch_tokens:
            items = self.database.list_batch_items(batch_token)
            if any(item["status"] in PENDING_REVIEW_STATUSES for item in items):
                self.database.mark_batch_status(batch_token, "review_in_progress")
            else:
                self.database.mark_batch_status(batch_token, "completed")

    def _get_inbox_root(self) -> Path | None:
        raw_path = self.config.paths.syncthing_inbox_path.strip()
        if not self.config.review_queue.enabled or not raw_path:
            return None
        inbox_root = Path(raw_path)
        inbox_root.mkdir(parents=True, exist_ok=True)
        return inbox_root

    def _is_file_stable(self, source_path: Path) -> bool:
        age_seconds = time.time() - source_path.stat().st_mtime
        return age_seconds >= self.config.review_queue.stable_file_age_seconds

    def _should_ignore_path(self, source_path: Path, inbox_root: Path) -> bool:
        try:
            relative_parts = source_path.relative_to(inbox_root).parts
        except ValueError:
            relative_parts = source_path.parts

        file_name = source_path.name.lower()
        if file_name in IGNORED_FILE_NAMES:
            return True

        for part in relative_parts:
            normalized = part.strip().lower()
            if not normalized:
                continue
            if normalized in IGNORED_PATH_PARTS:
                return True
            if normalized.startswith("."):
                return True
        return False

    def _ingest_source_file(self, source_path: Path) -> bool:
        inbox_root = self._get_inbox_root()
        if not inbox_root:
            return False

        modified_at = source_path.stat().st_mtime
        sha256_hash = compute_sha256(source_path)
        existing = self.database.find_pending_by_source_path(str(source_path))
        if (
            existing
            and str(existing["sha256_hash"]) == sha256_hash
            and float(existing.get("source_modified_at", 0) or 0) == modified_at
            and str(existing["status"]) != "error"
        ):
            return False

        mime_type, _ = mimetypes.guess_type(source_path.name)
        relative_path = source_path.relative_to(inbox_root)
        source_label = relative_path.parts[0] if len(relative_path.parts) > 1 else "Inbox"
        duplicate = self.duplicate_checker.check(sha256_hash)

        pending_item = PendingReviewItem(
            intake_source="syncthing",
            source_path=str(source_path),
            source_modified_at=modified_at,
            source_root=str(inbox_root),
            source_relative_path=str(relative_path).replace("\\", "/"),
            source_label=source_label,
            original_file_name=source_path.name,
            sha256_hash=sha256_hash,
            file_size=source_path.stat().st_size,
            mime_type=mime_type or "",
            status="pending_review",
            preview_type=self._guess_preview_type(source_path, mime_type or ""),
            preview_status="pending",
        )

        if duplicate.is_duplicate:
            archived_path = self.storage.archive_review_item(source_path, "duplicates")
            pending_item.status = "duplicate_skipped"
            pending_item.preview_status = "unsupported"
            pending_item.duplicate_saved_path = duplicate.existing_path or ""
            pending_item.saved_path = str(archived_path)
            self.database.insert_pending_item(pending_item)
            self.logger.info("Archived duplicate Syncthing file %s", source_path)
            return True

        item_id = self.database.insert_pending_item(pending_item)
        self._generate_preview(item_id, source_path, mime_type or "")
        self.logger.info("Queued Syncthing file for review: %s", source_path)
        return True

    def _guess_preview_type(self, source_path: Path, mime_type: str) -> str:
        suffix = source_path.suffix.lower()
        if mime_type.startswith("image/"):
            return "image_thumbnail"
        if mime_type.startswith("video/"):
            return "video_thumbnail"
        if suffix == ".pdf":
            return "pdf_first_page"
        return "file_icon"

    def _generate_preview(self, item_id: int, source_path: Path, mime_type: str) -> None:
        preview_type = self._guess_preview_type(source_path, mime_type)
        if preview_type == "image_thumbnail":
            self._generate_image_preview(item_id, source_path, preview_type)
            return
        if preview_type == "video_thumbnail":
            self._generate_video_preview(item_id, source_path, preview_type)
            return
        if preview_type == "pdf_first_page":
            self._generate_pdf_preview(item_id, source_path, preview_type)
            return

        self.database.update_pending_preview(
            item_id,
            thumbnail_path="",
            preview_type=preview_type,
            preview_status="unsupported",
        )

    def _generate_image_preview(self, item_id: int, source_path: Path, preview_type: str) -> None:
        if not self.config.review_queue.generate_image_thumbnails:
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="unsupported",
            )
            return

        if Image is None:
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="failed",
                preview_error="Pillow is not installed, so image thumbnails cannot be generated.",
            )
            return

        thumbnail_path = self.storage.build_review_thumbnail_path(item_id, source_path.name)
        try:
            with Image.open(source_path) as image:
                image = image.convert("RGB")
                width, height = image.size
                image.thumbnail((512, 512))
                thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(thumbnail_path, format="JPEG", quality=85)
            self.database.update_pending_preview(
                item_id,
                thumbnail_path=str(thumbnail_path),
                preview_type=preview_type,
                preview_status="ready",
                width=width,
                height=height,
            )
        except Exception as exc:  # pragma: no cover - file-format failures are environment-specific
            self.logger.warning("Thumbnail generation failed for %s: %s", source_path, exc)
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="failed",
                preview_error=str(exc),
            )

    def _generate_video_preview(self, item_id: int, source_path: Path, preview_type: str) -> None:
        if not self.config.review_queue.generate_video_thumbnails:
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="unsupported",
            )
            return

        if Image is None or imageio_ffmpeg is None:
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="failed",
                preview_error="imageio-ffmpeg and Pillow are required for video thumbnails.",
            )
            return

        thumbnail_path = self.storage.build_review_thumbnail_path(item_id, source_path.name)
        width: int | None = None
        height: int | None = None
        duration_seconds: float | None = None
        try:
            width, height, duration_seconds = self._read_video_metadata(source_path)
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            seek_seconds = self._select_video_seek_time(duration_seconds)
            command = [
                ffmpeg_exe,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{seek_seconds:.2f}",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                str(thumbnail_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "ffmpeg could not extract a video frame.")

            with Image.open(thumbnail_path) as image:
                image = image.convert("RGB")
                image.thumbnail((512, 512))
                image.save(thumbnail_path, format="JPEG", quality=85)

            self.database.update_pending_preview(
                item_id,
                thumbnail_path=str(thumbnail_path),
                preview_type=preview_type,
                preview_status="ready",
                width=width,
                height=height,
                duration_seconds=duration_seconds,
            )
        except Exception as exc:  # pragma: no cover - depends on runtime codecs and file formats
            self.logger.warning("Video preview generation failed for %s: %s", source_path, exc)
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="failed",
                preview_error=str(exc),
                width=width,
                height=height,
                duration_seconds=duration_seconds,
            )

    def _generate_pdf_preview(self, item_id: int, source_path: Path, preview_type: str) -> None:
        if not self.config.review_queue.generate_pdf_previews:
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="unsupported",
            )
            return

        if Image is None or fitz is None:
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="failed",
                preview_error="PyMuPDF and Pillow are required for PDF previews.",
            )
            return

        thumbnail_path = self.storage.build_review_thumbnail_path(item_id, source_path.name)
        width: int | None = None
        height: int | None = None
        page_count: int | None = None
        try:
            with fitz.open(source_path) as document:
                page_count = document.page_count
                page = document.load_page(0)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                width = pixmap.width
                height = pixmap.height
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                image.thumbnail((512, 512))
                thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(thumbnail_path, format="JPEG", quality=85)

            self.database.update_pending_preview(
                item_id,
                thumbnail_path=str(thumbnail_path),
                preview_type=preview_type,
                preview_status="ready",
                width=width,
                height=height,
                page_count=page_count,
            )
        except Exception as exc:  # pragma: no cover - depends on runtime PDFs
            self.logger.warning("PDF preview generation failed for %s: %s", source_path, exc)
            self.database.update_pending_preview(
                item_id,
                thumbnail_path="",
                preview_type=preview_type,
                preview_status="failed",
                preview_error=str(exc),
                width=width,
                height=height,
                page_count=page_count,
            )

    def _with_preview_context(self, item: dict[str, Any]) -> dict[str, Any]:
        preview_path = str(item.get("thumbnail_path", "") or "")
        item["thumbnail_url"] = f"/review/thumbnail/{item['id']}" if preview_path else ""
        item["display_size_mb"] = round(int(item.get("file_size", 0)) / (1024 * 1024), 2)
        suffix = Path(str(item.get("original_file_name", ""))).suffix.lstrip(".").upper()
        item["file_label"] = suffix or "FILE"
        item["display_duration"] = self._format_duration(float(item["duration_seconds"])) if item.get("duration_seconds") else ""
        item["display_page_count"] = int(item["page_count"]) if item.get("page_count") else 0
        item["preview_ready"] = str(item.get("preview_status", "")) == "ready"
        return item

    def _read_video_metadata(self, source_path: Path) -> tuple[int | None, int | None, float | None]:
        if imageio_ffmpeg is None:
            return None, None, None

        reader = imageio_ffmpeg.read_frames(str(source_path), pix_fmt="rgb24")
        try:
            metadata = next(reader)
        finally:
            close_method = getattr(reader, "close", None)
            if callable(close_method):
                close_method()

        source_size = metadata.get("source_size") or metadata.get("size") or (None, None)
        width = int(source_size[0]) if source_size and source_size[0] else None
        height = int(source_size[1]) if source_size and source_size[1] else None
        duration = metadata.get("duration")
        duration_seconds = float(duration) if duration not in (None, 0, "0") else None
        return width, height, duration_seconds

    def _select_video_seek_time(self, duration_seconds: float | None) -> float:
        if not duration_seconds or duration_seconds <= 2:
            return 0.0
        return min(max(duration_seconds * 0.15, 0.5), duration_seconds - 0.5)

    def _split_folder_path(self, folder_path_value: str) -> list[str]:
        return [part for part in str(folder_path_value or "").split("/") if part]

    def _format_destination_label(self, category_name: str, folder_path: list[str]) -> str:
        return " / ".join([category_name, *folder_path]) if folder_path else category_name

    def _build_destination_item(
        self,
        *,
        category: str,
        folder_path: list[str],
        is_favorite: bool,
        label: str = "",
        use_count: int = 0,
    ) -> dict[str, Any]:
        destination_value = self._format_destination_label(category, folder_path)
        return {
            "category": category,
            "folder_path": folder_path,
            "folder_path_value": "/".join(folder_path),
            "destination_value": destination_value,
            "label": label or destination_value,
            "is_favorite": is_favorite,
            "use_count": use_count,
        }

    def _format_duration(self, duration_seconds: float) -> str:
        total_seconds = max(0, int(round(duration_seconds)))
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def _build_review_url(self, batch_token: str) -> str:
        return f"{self.build_review_dashboard_url()}/batch/{batch_token}"

    def _build_notification_text(
        self,
        ready_items: list[dict[str, Any]],
        review_url: str,
        *,
        is_reminder: bool = False,
    ) -> str:
        source_labels = sorted({str(item["source_label"]) for item in ready_items if str(item["source_label"]).strip()})
        label_summary = ", ".join(source_labels[:4])
        if len(source_labels) > 4:
            label_summary += ", ..."
        lines = [
            (
                f"Reminder: {len(ready_items)} Syncthing file(s) are still waiting to be sorted."
                if is_reminder
                else f"{len(ready_items)} Syncthing file(s) are ready to sort."
            ),
        ]
        if label_summary:
            lines.append(f"Sources: {label_summary}")
        lines.extend(
            [
                "",
                "Use the button below to open the private review page and batch-save the files.",
            ]
        )
        return "\n".join(lines)

    def _send_telegram_message(
        self,
        chat_id: int,
        text: str,
        *,
        button_text: str | None = None,
        button_url: str | None = None,
    ) -> bool:
        try:
            if self.config.local_bot_api.enabled:
                base_url = self.config.local_bot_api.base_url.rstrip("/")
                token_suffix = self.config.telegram_bot_token
                if not base_url.endswith(token_suffix):
                    base_url = f"{base_url}{token_suffix}"
            else:
                base_url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}"
            endpoint = f"{base_url}/sendMessage"
            payload_dict = {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": "true",
            }
            if button_text and button_url:
                payload_dict["reply_markup"] = json.dumps(
                    {
                        "inline_keyboard": [
                            [
                                {
                                    "text": button_text,
                                    "url": button_url,
                                }
                            ]
                        ]
                    }
                )
            payload = parse.urlencode(payload_dict).encode("utf-8")
            req = request.Request(endpoint, data=payload, method="POST")
            with request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
            decoded = json.loads(body)
            return bool(decoded.get("ok"))
        except Exception as exc:  # pragma: no cover - network failures are environment-specific
            self.logger.warning("Could not send Telegram review notification to %s: %s", chat_id, exc)
            return False
