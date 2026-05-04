from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.config import AppConfig, load_config, save_config
from src.database import Database
from src.duplicates import DuplicateChecker
from src.models import MediaRecord, QueuedMediaItem, UserSession
from src.storage import StorageService
from src.utils import clean_name, compute_sha256, sanitize_filename


class MediaSorterBot:
    MAX_RECENT_DESTINATIONS = 5

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

        self.sessions: dict[int, UserSession] = {}
        self.user_queues: dict[int, deque[QueuedMediaItem]] = defaultdict(deque)
        self.user_locks: dict[int, asyncio.Lock] = {}

        # TODO: For real multi-server operation with one shared bot token, move routing
        # out of local polling and into a central dispatcher, webhook gateway, or a
        # server_id-aware update assignment layer so only one installation claims each file.
        self.application = self._build_application()
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("server", self.start_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(
            MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, self.handle_media_message)
        )
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))

    def _build_application(self) -> Application:
        builder = Application.builder().token(self.config.telegram_bot_token)
        if self.config.local_bot_api.enabled:
            builder = (
                builder.base_url(self.config.local_bot_api.base_url)
                .base_file_url(self.config.local_bot_api.base_file_url)
                .local_mode(True)
            )
        return builder.build()

    def run(self) -> None:
        if self.config.local_bot_api.enabled:
            self.logger.info(
                "Starting Telegram polling bot in local Bot API mode via %s",
                self.config.local_bot_api.base_url,
            )
        self.logger.info("Starting Telegram polling bot for server_id=%s", self.config.server.server_id)
        self.application.run_polling(drop_pending_updates=False)

    def _get_session(self, user_id: int) -> UserSession:
        if user_id not in self.sessions:
            self.sessions[user_id] = UserSession()
        return self.sessions[user_id]

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self.user_locks:
            self.user_locks[user_id] = asyncio.Lock()
        return self.user_locks[user_id]

    def _is_allowed_user(self, user_id: int) -> bool:
        return user_id in self.config.security.allowed_telegram_user_ids

    def _ensure_active_server(self, user_id: int) -> UserSession:
        session = self._get_session(user_id)
        if session.active_server_id != self.config.server.server_id:
            session.active_server_id = self.config.server.server_id
        return session

    def _refresh_runtime_config(self) -> None:
        latest_config = load_config(self.config_path)
        self.config = latest_config
        self.storage.refresh_config(latest_config)
        self.duplicate_checker.duplicate_action = latest_config.behavior.duplicate_action

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return

        if not self._is_allowed_user(user.id):
            if update.effective_message:
                await update.effective_message.reply_text("You are not authorized to use this bot.")
            return

        self._ensure_active_server(user.id)
        await update.effective_message.reply_text(
            (
                "Server auto-activated for this chat session.\n"
                f"Name: {self.config.server.server_name}\n"
                f"ID: {self.config.server.server_id}\n\n"
                "You can now send photos, videos, or documents to classify and save."
            )
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user = update.effective_user
        if not query or not user:
            return

        if not self._is_allowed_user(user.id):
            await query.answer("Not authorized.", show_alert=True)
            return

        data = query.data or ""
        if data == "server:activate":
            session = self._get_session(user.id)
            session.active_server_id = self.config.server.server_id
            await query.answer("Server activated.")
            await query.edit_message_text(
                (
                    f"Active server set to {self.config.server.server_name}.\n"
                    "You can now send photos, videos, or documents to classify and save."
                )
            )
            return

        session = self._ensure_active_server(user.id)

        if not session.current_item:
            await query.answer("No file is waiting for classification.", show_alert=True)
            return

        if data.startswith("category:"):
            await self._handle_category_selection(query, user.id, data)
        elif data.startswith("recent:"):
            await self._handle_recent_destination_selection(query, context.bot, user.id, data)
        elif data.startswith("folder:"):
            await self._handle_folder_selection(query, context.bot, user.id, data)
        elif data.startswith("confirm:"):
            await self._handle_destination_confirmation(query, context.bot, user.id, data)
        else:
            await query.answer("Unsupported action.", show_alert=True)

    async def handle_media_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return

        if not self._is_allowed_user(user.id):
            await message.reply_text("You are not authorized to use this bot.")
            return

        session = self._ensure_active_server(user.id)

        media_info = self._extract_media_info(message)
        if not media_info:
            await message.reply_text("This message does not contain a supported photo, video, or document.")
            return

        try:
            telegram_file = await context.bot.get_file(media_info["telegram_file_id"])
            temp_path = self.storage.build_temp_download_path(media_info["original_file_name"])
            await telegram_file.download_to_drive(custom_path=str(temp_path))
            sha256_hash = compute_sha256(temp_path)
        except Exception as exc:
            self.logger.exception("Failed to download incoming media: %s", exc)
            self._record_error(
                user_id=user.id,
                username=user.username or user.full_name or "",
                chat_id=message.chat_id,
                message_id=message.message_id,
                telegram_file_id=media_info["telegram_file_id"],
                original_file_name=media_info["original_file_name"],
                sha256_hash="",
                category="",
                folder_path="",
            )
            await message.reply_text("The file could not be downloaded. Please try again.")
            return

        duplicate = self.duplicate_checker.check(sha256_hash)
        if duplicate.is_duplicate:
            self._record_status(
                item=QueuedMediaItem(
                    telegram_file_id=media_info["telegram_file_id"],
                    media_kind=media_info["media_kind"],
                    original_file_name=media_info["original_file_name"],
                    temp_path=temp_path,
                    sha256_hash=sha256_hash,
                    user_id=user.id,
                    username=user.username or user.full_name or "",
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                ),
                saved_path=duplicate.existing_path or "",
                status="skipped",
                category="",
                folder_path="",
            )
            self.storage.cleanup_temp_file(temp_path)
            await message.reply_text(
                f"Duplicate skipped. Already saved at:\n{duplicate.existing_path}"
            )
            if self.config.behavior.delete_telegram_message_after_save:
                try:
                    await context.bot.delete_message(chat_id=message.chat_id, message_id=message.message_id)
                except Exception as exc:
                    self.logger.warning("Duplicate skipped but could not delete Telegram message: %s", exc)
            return

        item = QueuedMediaItem(
            telegram_file_id=media_info["telegram_file_id"],
            media_kind=media_info["media_kind"],
            original_file_name=media_info["original_file_name"],
            temp_path=temp_path,
            sha256_hash=sha256_hash,
            user_id=user.id,
            username=user.username or user.full_name or "",
            chat_id=message.chat_id,
            message_id=message.message_id,
        )

        lock = self._get_lock(user.id)
        async with lock:
            user_queue = self.user_queues[user.id]
            user_queue.append(item)
            session.batch_total += 1

            if session.current_item is None:
                await self._start_next_item(user.id, context.bot)
            else:
                await message.reply_text(
                    f"Added to your queue. {len(user_queue)} item(s) waiting behind the current file."
                )

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return

        session = self._get_session(user.id)
        if session.stage != "awaiting_new_folder" or not session.current_item:
            return

        raw_folder_name = message.text.strip()
        if not raw_folder_name:
            await message.reply_text("Please send a valid folder name.")
            return
        new_folder_name = clean_name(raw_folder_name)

        lock = self._get_lock(user.id)
        async with lock:
            current_item = session.current_item
            if not current_item:
                await message.reply_text("No file is currently waiting for a new folder.")
                return

            current_item.folder_path = [*current_item.folder_path, new_folder_name]
            self._ensure_folder_path_in_memory(current_item.category, current_item.folder_path)
            self._persist_folder_path_to_config(current_item.category, current_item.folder_path)
            session.announce_new_folder_on_save = True
            remaining_count = self._get_remaining_queue_count(user.id)
            if remaining_count > 0:
                session.stage = "confirm_destination"
                await message.reply_text(
                    self._build_destination_confirmation_text(
                        current_item,
                        selection_prefix="New folder created",
                        remaining_count=remaining_count,
                    ),
                    reply_markup=self._build_destination_confirmation_keyboard(),
                )
                return

            session.stage = None
            await self._finalize_current_item(user.id, context.bot, announce_new_folder=True)

    def _extract_media_info(self, message) -> Optional[dict[str, str]]:
        if message.photo:
            extension = ".jpg"
            original_name = self._select_original_file_name(
                provided_name=None,
                message_id=message.message_id,
                media_kind="photo",
                extension=extension,
            )
            return {
                "telegram_file_id": message.photo[-1].file_id,
                "original_file_name": original_name,
                "media_kind": "photo",
            }

        if message.video:
            extension = (
                Path(message.video.file_name or "").suffix
                or mimetypes.guess_extension(message.video.mime_type or "")
                or ".mp4"
            )
            original_name = self._select_original_file_name(
                provided_name=message.video.file_name,
                message_id=message.message_id,
                media_kind="video",
                extension=extension,
            )
            return {
                "telegram_file_id": message.video.file_id,
                "original_file_name": original_name,
                "media_kind": "video",
            }

        if message.document:
            extension = (
                Path(message.document.file_name or "").suffix
                or mimetypes.guess_extension(message.document.mime_type or "")
                or ".bin"
            )
            original_name = self._select_original_file_name(
                provided_name=message.document.file_name,
                message_id=message.message_id,
                media_kind="document",
                extension=extension,
            )
            return {
                "telegram_file_id": message.document.file_id,
                "original_file_name": original_name,
                "media_kind": "document",
            }

        return None

    def _select_original_file_name(
        self,
        provided_name: Optional[str],
        message_id: int,
        media_kind: str,
        extension: str,
    ) -> str:
        if self.config.behavior.keep_original_filename and provided_name:
            return sanitize_filename(provided_name)
        safe_extension = extension if extension.startswith(".") else f".{extension}"
        return sanitize_filename(f"{media_kind}_{message_id}{safe_extension}")

    async def _start_next_item(self, user_id: int, bot) -> None:
        self._refresh_runtime_config()
        session = self._get_session(user_id)
        user_queue = self.user_queues[user_id]
        if not user_queue:
            session.current_item = None
            session.stage = None
            session.batch_total = 0
            session.batch_processed = 0
            session.announce_new_folder_on_save = False
            self._clear_batch_destination(session)
            return

        session.current_item = user_queue.popleft()
        session.announce_new_folder_on_save = False
        current_index = session.batch_processed + 1
        total = session.batch_total

        batch_destination = self._get_batch_destination(session)
        if batch_destination:
            category_name, folder_path = batch_destination
            session.current_item.category = category_name
            session.current_item.folder_path = list(folder_path)
            session.stage = None
            await bot.send_message(
                chat_id=session.current_item.chat_id,
                text=(
                    f"Processing {current_index} of {total}\n"
                    f"File: {session.current_item.original_file_name}\n\n"
                    f"Using folder for remaining files:\n"
                    f"{self._format_destination(category_name, session.current_item.folder_path)}"
                ),
            )
            await self._finalize_current_item(user_id, bot, auto_applied_destination=True)
            return

        session.stage = "category"

        keyboard = self._build_start_keyboard(session)
        await bot.send_message(
            chat_id=session.current_item.chat_id,
            text=(
                f"Processing {current_index} of {total}\n"
                f"File: {session.current_item.original_file_name}\n\n"
                "Choose a category or reuse a recent save location:"
            ),
            reply_markup=keyboard,
        )

    def _build_start_keyboard(self, session: UserSession) -> InlineKeyboardMarkup:
        buttons: list[list[InlineKeyboardButton]] = []

        for index, (category_name, folder_path) in enumerate(self._get_valid_recent_destinations(session)):
            label = self._format_recent_destination_label(category_name, list(folder_path))
            buttons.append([InlineKeyboardButton(label, callback_data=f"recent:{index}")])

        for index, category in enumerate(self.config.categories):
            buttons.append([InlineKeyboardButton(category.name, callback_data=f"category:{index}")])

        return InlineKeyboardMarkup(buttons)

    def _get_valid_recent_destinations(self, session: UserSession) -> list[tuple[str, tuple[str, ...]]]:
        valid_destinations = [
            (category_name, folder_path)
            for category_name, folder_path in session.recent_destinations
            if self._folder_path_exists(category_name, list(folder_path))
        ]
        if len(valid_destinations) != len(session.recent_destinations):
            session.recent_destinations = valid_destinations
        return valid_destinations

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

    def _format_recent_destination_label(self, category_name: str, folder_path: list[str]) -> str:
        destination = self._format_destination(category_name, folder_path)
        return destination if len(destination) <= 55 else f"...{destination[-52:]}"

    async def _handle_recent_destination_selection(self, query, bot, user_id: int, data: str) -> None:
        self._refresh_runtime_config()
        session = self._get_session(user_id)
        current_item = session.current_item
        if not current_item:
            await query.answer("No active file.", show_alert=True)
            return

        recent_destinations = self._get_valid_recent_destinations(session)
        recent_index = int(data.split(":", maxsplit=1)[1])
        if recent_index < 0 or recent_index >= len(recent_destinations):
            await query.answer("That recent location is no longer available.", show_alert=True)
            return

        category_name, folder_path = recent_destinations[recent_index]
        current_item.category = category_name
        current_item.folder_path = list(folder_path)

        await self._confirm_or_finalize_selected_destination(
            user_id=user_id,
            bot=bot,
            source_message=query,
            selection_prefix="Recent destination selected",
        )

    async def _handle_category_selection(self, query, user_id: int, data: str) -> None:
        self._refresh_runtime_config()
        session = self._get_session(user_id)
        current_item = session.current_item
        if not current_item:
            await query.answer("No active file.", show_alert=True)
            return

        category_index = int(data.split(":", maxsplit=1)[1])
        if category_index < 0 or category_index >= len(self.config.categories):
            await query.answer("Invalid category.", show_alert=True)
            return

        category = self.config.categories[category_index]
        current_item.category = category.name
        current_item.folder_path = []
        session.stage = "folder"

        await query.answer()
        await query.edit_message_text(
            self._build_folder_prompt_text(current_item),
            reply_markup=self._build_folder_keyboard(current_item),
        )

    async def _handle_folder_selection(self, query, bot, user_id: int, data: str) -> None:
        self._refresh_runtime_config()
        session = self._get_session(user_id)
        current_item = session.current_item
        if not current_item or not current_item.category:
            await query.answer("Category must be selected first.", show_alert=True)
            return

        action = data.split(":", maxsplit=2)
        command = action[1] if len(action) > 1 else ""

        if command == "save":
            await self._confirm_or_finalize_selected_destination(
                user_id=user_id,
                bot=bot,
                source_message=query,
                selection_prefix="Destination selected",
            )
            return

        if command == "new":
            session.stage = "awaiting_new_folder"
            await query.answer()
            await query.edit_message_text(
                "Send the new folder name for:\n"
                f"{self._format_destination(current_item.category, current_item.folder_path)}"
            )
            return

        if command == "up":
            if current_item.folder_path:
                current_item.folder_path.pop()
            session.stage = "folder"
            await query.answer()
            await query.edit_message_text(
                self._build_folder_prompt_text(current_item),
                reply_markup=self._build_folder_keyboard(current_item),
            )
            return

        if command != "open" or len(action) != 3:
            await query.answer("Unsupported folder action.", show_alert=True)
            return

        options = self._get_folder_options(current_item.category, current_item.folder_path)
        folder_index = int(action[2])
        if folder_index < 0 or folder_index >= len(options):
            await query.answer("Invalid folder.", show_alert=True)
            return

        current_item.folder_path.append(options[folder_index].name)
        session.stage = "folder"
        await query.answer()
        await query.edit_message_text(
            self._build_folder_prompt_text(current_item),
            reply_markup=self._build_folder_keyboard(current_item),
        )

    def _build_folder_prompt_text(self, item: QueuedMediaItem) -> str:
        return (
            f"Category selected: {item.category}\n"
            f"Current folder: {self._format_destination(item.category or '', item.folder_path)}\n\n"
            "Open a folder below or save into the current folder."
        )

    def _build_folder_keyboard(self, item: QueuedMediaItem) -> InlineKeyboardMarkup:
        buttons = [
            [InlineKeyboardButton(folder.name, callback_data=f"folder:open:{index}")]
            for index, folder in enumerate(self._get_folder_options(item.category or "", item.folder_path))
        ]

        select_label = "Use Category Root" if not item.folder_path else "Use This Folder"
        buttons.append([InlineKeyboardButton(select_label, callback_data="folder:save")])

        if item.folder_path:
            buttons.append([InlineKeyboardButton("Up One Level", callback_data="folder:up")])

        if self.config.behavior.allow_new_folder:
            buttons.append([InlineKeyboardButton("New Folder Here", callback_data="folder:new")])

        return InlineKeyboardMarkup(buttons)

    def _get_folder_options(self, category_name: str, current_path: list[str]) -> list[FolderNode]:
        category = self.config.get_category(category_name)
        if not category:
            return []

        nodes = category.folders
        for path_part in current_path:
            match = next((node for node in nodes if node.name == path_part), None)
            if not match:
                return []
            nodes = match.folders
        return nodes

    def _ensure_folder_path_in_memory(self, category_name: str | None, folder_path: list[str]) -> None:
        if not category_name:
            return

        self.config.ensure_folder_path(category_name, folder_path)

    def _persist_folder_path_to_config(self, category_name: str | None, folder_path: list[str]) -> None:
        if not category_name:
            return

        try:
            latest_config = load_config(self.config_path)
            changed = latest_config.ensure_folder_path(category_name, folder_path)
            if changed:
                save_config(latest_config, self.config_path, create_backup=False)
            self.config = latest_config
        except Exception as exc:
            self.logger.warning(
                "Saved folder on disk but could not persist it to config.yaml for %s/%s: %s",
                category_name,
                "/".join(folder_path),
                exc,
            )

    def _format_destination(self, category_name: str, folder_path: list[str]) -> str:
        if not category_name:
            return "(not selected)"
        parts = [category_name, *folder_path]
        return " / ".join(parts)

    def _remember_recent_destination(self, session: UserSession, category_name: str, folder_path: list[str]) -> None:
        destination = (category_name, tuple(folder_path))
        updated = [entry for entry in session.recent_destinations if entry != destination]
        updated.insert(0, destination)
        session.recent_destinations = updated[: self.MAX_RECENT_DESTINATIONS]

    def _clear_batch_destination(self, session: UserSession) -> None:
        session.batch_destination_category = None
        session.batch_destination_folder_path = ()
        session.batch_destination_remaining = 0

    def _set_batch_destination(self, session: UserSession, category_name: str, folder_path: list[str], remaining: int) -> None:
        session.batch_destination_category = category_name
        session.batch_destination_folder_path = tuple(folder_path)
        session.batch_destination_remaining = max(0, remaining)

    def _get_batch_destination(self, session: UserSession) -> Optional[tuple[str, tuple[str, ...]]]:
        if not session.batch_destination_category or session.batch_destination_remaining <= 0:
            self._clear_batch_destination(session)
            return None

        folder_path = list(session.batch_destination_folder_path)
        if not self._folder_path_exists(session.batch_destination_category, folder_path):
            self._clear_batch_destination(session)
            return None

        return session.batch_destination_category, session.batch_destination_folder_path

    def _get_remaining_queue_count(self, user_id: int) -> int:
        return len(self.user_queues[user_id])

    def _build_destination_confirmation_text(
        self,
        item: QueuedMediaItem,
        selection_prefix: str,
        remaining_count: int,
    ) -> str:
        destination = self._format_destination(item.category or "", item.folder_path)
        return (
            f"{selection_prefix}: {destination}\n\n"
            f"Apply this folder to the remaining {remaining_count} file(s) in the current queue?"
        )

    def _build_destination_confirmation_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("This File Only", callback_data="confirm:once")],
                [InlineKeyboardButton("Use for Remaining Files", callback_data="confirm:remaining")],
            ]
        )

    async def _confirm_or_finalize_selected_destination(
        self,
        user_id: int,
        bot,
        source_message,
        selection_prefix: str,
    ) -> None:
        session = self._get_session(user_id)
        current_item = session.current_item
        if not current_item or not current_item.category:
            return

        remaining_count = self._get_remaining_queue_count(user_id)
        if remaining_count > 0:
            session.stage = "confirm_destination"
            await source_message.answer()
            await source_message.edit_message_text(
                self._build_destination_confirmation_text(current_item, selection_prefix, remaining_count),
                reply_markup=self._build_destination_confirmation_keyboard(),
            )
            return

        session.stage = None
        await source_message.answer()
        await source_message.edit_message_text(
            f"{selection_prefix}: {self._format_destination(current_item.category, current_item.folder_path)}"
        )

        lock = self._get_lock(user_id)
        async with lock:
            await self._finalize_current_item(user_id, bot)

    async def _handle_destination_confirmation(self, query, bot, user_id: int, data: str) -> None:
        session = self._get_session(user_id)
        current_item = session.current_item
        if not current_item or not current_item.category:
            await query.answer("No active file.", show_alert=True)
            return

        command = data.split(":", maxsplit=1)[1] if ":" in data else ""
        if command not in {"once", "remaining"}:
            await query.answer("Unsupported confirmation action.", show_alert=True)
            return

        if command == "remaining":
            self._set_batch_destination(
                session,
                current_item.category,
                current_item.folder_path,
                remaining=self._get_remaining_queue_count(user_id),
            )
            confirmation_text = (
                f"Destination selected for this file and the remaining "
                f"{session.batch_destination_remaining} file(s): "
                f"{self._format_destination(current_item.category, current_item.folder_path)}"
            )
        else:
            confirmation_text = (
                f"Destination selected: "
                f"{self._format_destination(current_item.category, current_item.folder_path)}"
            )

        session.stage = None
        await query.answer()
        await query.edit_message_text(confirmation_text)

        lock = self._get_lock(user_id)
        async with lock:
            await self._finalize_current_item(
                user_id,
                bot,
                announce_new_folder=session.announce_new_folder_on_save,
            )

    async def _finalize_current_item(
        self,
        user_id: int,
        bot,
        announce_new_folder: bool = False,
        auto_applied_destination: bool = False,
    ) -> None:
        session = self._get_session(user_id)
        item = session.current_item
        if not item or not item.category:
            return

        folder_path_text = "/".join(item.folder_path)

        try:
            final_path = self.storage.save_media(
                temp_path=item.temp_path,
                original_file_name=item.original_file_name,
                category=item.category,
                folder_path=item.folder_path,
            )
            self._remember_recent_destination(session, item.category, item.folder_path)
            self._record_status(
                item=item,
                saved_path=str(final_path),
                status="saved",
                category=item.category,
                folder_path=folder_path_text,
            )
            self.storage.cleanup_temp_file(item.temp_path)

            save_message = f"Saved to:\n{final_path}"
            if announce_new_folder:
                save_message = (
                    f"New folder created: {self._format_destination(item.category, item.folder_path)}\n\n"
                    f"{save_message}"
                )
            if auto_applied_destination:
                save_message = (
                    f"Used remaining-files folder: {self._format_destination(item.category, item.folder_path)}\n\n"
                    f"{save_message}"
                )
            await bot.send_message(chat_id=item.chat_id, text=save_message)

            if self.config.behavior.delete_telegram_message_after_save:
                try:
                    await bot.delete_message(chat_id=item.chat_id, message_id=item.message_id)
                except Exception as exc:
                    self.logger.warning("Saved file but could not delete Telegram message: %s", exc)
            if auto_applied_destination and session.batch_destination_remaining > 0:
                session.batch_destination_remaining -= 1
            if session.batch_destination_remaining <= 0:
                self._clear_batch_destination(session)
        except Exception as exc:
            self.logger.exception("Failed to save media item: %s", exc)
            self._clear_batch_destination(session)
            self._record_error(
                user_id=item.user_id,
                username=item.username,
                chat_id=item.chat_id,
                message_id=item.message_id,
                telegram_file_id=item.telegram_file_id,
                original_file_name=item.original_file_name,
                sha256_hash=item.sha256_hash,
                category=item.category or "",
                folder_path=folder_path_text,
            )
            await bot.send_message(
                chat_id=item.chat_id,
                text="The file could not be saved locally, so the Telegram message was kept.",
            )
        finally:
            if item.temp_path.exists():
                self.storage.cleanup_temp_file(item.temp_path)
            session.current_item = None
            session.stage = None
            session.announce_new_folder_on_save = False
            session.batch_processed += 1
            await self._start_next_item(user_id, bot)

    def _record_status(
        self,
        item: QueuedMediaItem,
        saved_path: str,
        status: str,
        category: str,
        folder_path: str,
    ) -> None:
        self.database.insert_record(
            MediaRecord(
                server_id=self.config.server.server_id,
                sha256_hash=item.sha256_hash,
                original_file_name=item.original_file_name,
                telegram_file_id=item.telegram_file_id,
                saved_path=saved_path,
                category=category,
                folder_path=folder_path,
                received_from_user_id=item.user_id,
                received_from_username=item.username,
                telegram_chat_id=item.chat_id,
                telegram_message_id=item.message_id,
                date_received=item.date_received.isoformat(),
                status=status,
            )
        )

    def _record_error(
        self,
        user_id: int,
        username: str,
        chat_id: int,
        message_id: int,
        telegram_file_id: str,
        original_file_name: str,
        sha256_hash: str,
        category: str,
        folder_path: str,
    ) -> None:
        self.database.insert_record(
            MediaRecord(
                server_id=self.config.server.server_id,
                sha256_hash=sha256_hash,
                original_file_name=original_file_name,
                telegram_file_id=telegram_file_id,
                saved_path="",
                category=category,
                folder_path=folder_path,
                received_from_user_id=user_id,
                received_from_username=username,
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
                date_received=datetime.utcnow().isoformat(),
                status="error",
            )
        )
