from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import (
    AppConfig,
    BehaviorConfig,
    CategoryConfig,
    FolderConfig,
    FolderNode,
    LocalBotAPIConfig,
    LoggingConfig,
    PathsConfig,
    ReviewQueueConfig,
    SecurityConfig,
    ServerConfig,
    WebUIConfig,
    load_config,
    save_config,
)
from src.database import Database
from src.duplicates import DuplicateChecker
from src.review_queue import ReviewQueueService
from src.storage import StorageService
from src.utils import clean_name, ensure_directory, split_folder_input


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_web_app(
    config_path: Path,
    *,
    database: Database | None = None,
    storage: StorageService | None = None,
    duplicate_checker: DuplicateChecker | None = None,
) -> FastAPI:
    config = load_config(config_path)
    database = database or Database(Path(config.paths.database_path))
    storage = storage or StorageService(config)
    duplicate_checker = duplicate_checker or DuplicateChecker(database, config.behavior.duplicate_action)
    review_queue = ReviewQueueService(config, config_path, database, storage, duplicate_checker)

    app = FastAPI(title="Media Sorter Bot Config Editor")
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        latest_config = load_config(config_path)
        return _render_config_template(
            request,
            latest_config,
            success_message=None,
            error_message=None,
            backup_path=None,
        )

    @app.post("/save", response_class=HTMLResponse)
    async def save(request: Request) -> HTMLResponse:
        form = await request.form()

        try:
            config_to_save = _build_config_from_form(form)
            _validate_config(config_to_save)
            backup_path = save_config(config_to_save, config_path, create_backup=True)

            if str(form.get("create_missing_folders", "")) == "on":
                _create_missing_folders(config_to_save)

            return _render_config_template(
                request,
                config_to_save,
                success_message="Configuration saved successfully.",
                error_message=None,
                backup_path=str(backup_path) if backup_path else None,
            )
        except Exception as exc:
            current_config = load_config(config_path)
            return _render_config_template(
                request,
                current_config,
                success_message=None,
                error_message=str(exc),
                backup_path=None,
            )

    @app.post("/import-folders", response_class=HTMLResponse)
    async def import_folders(request: Request) -> HTMLResponse:
        form = await request.form()

        try:
            config_to_save = _build_config_from_form(form)
            _validate_config(config_to_save)

            selected_categories = _parse_selected_categories_json(form.get("import_categories_json", "[]"))
            if not selected_categories:
                raise ValueError("Select at least one category to import from disk.")

            imported_nodes, scanned_categories = _merge_selected_categories_from_disk(
                config_to_save,
                selected_categories,
            )
            backup_path = save_config(config_to_save, config_path, create_backup=True)

            if imported_nodes:
                success_message = (
                    f"Imported {imported_nodes} folder(s) from disk into "
                    f"{', '.join(scanned_categories)}. Existing config entries were preserved."
                )
            else:
                success_message = (
                    f"No new folders were found on disk for {', '.join(scanned_categories)}. "
                    "Existing config entries were preserved."
                )

            return _render_config_template(
                request,
                config_to_save,
                success_message=success_message,
                error_message=None,
                backup_path=str(backup_path) if backup_path else None,
            )
        except Exception as exc:
            current_config = load_config(config_path)
            return _render_config_template(
                request,
                current_config,
                success_message=None,
                error_message=str(exc),
                backup_path=None,
            )

    @app.get("/review", response_class=HTMLResponse)
    async def review_dashboard(request: Request) -> HTMLResponse:
        return _render_review_template(
            request,
            review_queue,
            batch_token=None,
            success_message=None,
            error_message=None,
        )

    @app.get("/review/batch/{batch_token}", response_class=HTMLResponse)
    async def review_batch(request: Request, batch_token: str) -> HTMLResponse:
        return _render_review_template(
            request,
            review_queue,
            batch_token=batch_token,
            success_message=None,
            error_message=None,
        )

    @app.post("/review/batch/{batch_token}/save", response_class=HTMLResponse)
    async def review_batch_save(request: Request, batch_token: str) -> HTMLResponse:
        form = await request.form()
        item_ids = [int(value) for value in form.getlist("selected_item_ids") if str(value).strip()]
        destination_value = _resolve_review_destination(form)

        try:
            if not item_ids:
                raise ValueError("Select at least one file to save.")
            result = review_queue.save_items(item_ids, destination_value)
            success_message = f"Saved {result['saved_count']} file(s) to {result['destination']}."
            if result["failed_count"]:
                success_message += f" {result['failed_count']} file(s) failed and were marked as errors."
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=success_message,
                error_message=None,
            )
        except Exception as exc:
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=None,
                error_message=str(exc),
            )

    @app.post("/review/batch/{batch_token}/skip", response_class=HTMLResponse)
    async def review_batch_skip(request: Request, batch_token: str) -> HTMLResponse:
        form = await request.form()
        item_ids = [int(value) for value in form.getlist("selected_item_ids") if str(value).strip()]

        try:
            if not item_ids:
                raise ValueError("Select at least one file to skip.")
            result = review_queue.skip_items(item_ids)
            success_message = f"Skipped {result['skipped_count']} file(s)."
            if result["failed_count"]:
                success_message += f" {result['failed_count']} file(s) failed and were marked as errors."
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=success_message,
                error_message=None,
            )
        except Exception as exc:
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=None,
                error_message=str(exc),
            )

    @app.post("/review/batch/{batch_token}/favorite/add", response_class=HTMLResponse)
    async def review_batch_add_favorite(request: Request, batch_token: str) -> HTMLResponse:
        form = await request.form()
        destination_value = _resolve_review_destination(form)

        try:
            favorite = review_queue.save_favorite_destination(destination_value)
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=f"Saved favorite destination: {favorite['label']}.",
                error_message=None,
            )
        except Exception as exc:
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=None,
                error_message=str(exc),
            )

    @app.post("/review/batch/{batch_token}/favorite/remove", response_class=HTMLResponse)
    async def review_batch_remove_favorite(request: Request, batch_token: str) -> HTMLResponse:
        form = await request.form()
        destination_value = _resolve_review_destination(form)

        try:
            favorite = review_queue.remove_favorite_destination(destination_value)
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=f"Removed favorite destination: {favorite['label']}.",
                error_message=None,
            )
        except Exception as exc:
            return _render_review_template(
                request,
                review_queue,
                batch_token=batch_token,
                success_message=None,
                error_message=str(exc),
            )

    @app.get("/review/thumbnail/{item_id}")
    async def review_thumbnail(item_id: int):
        item = review_queue.database.get_pending_item(item_id)
        if not item:
            return HTMLResponse(status_code=404, content="Item not found.")

        raw_thumbnail_path = str(item.get("thumbnail_path", "") or "").strip()
        if not raw_thumbnail_path:
            return HTMLResponse(status_code=404, content="Thumbnail not found.")

        thumbnail_path = Path(raw_thumbnail_path)
        if not thumbnail_path.exists():
            return HTMLResponse(status_code=404, content="Thumbnail not found.")
        return FileResponse(thumbnail_path)

    return app


def _render_config_template(
    request: Request,
    config: AppConfig,
    success_message: str | None,
    error_message: str | None,
    backup_path: str | None,
) -> HTMLResponse:
    categories_payload = [category.to_dict() for category in config.categories]
    return TEMPLATES.TemplateResponse(
        request=request,
        name="config.html",
        context={
            "request": request,
            "config": config,
            "allowed_user_ids_text": "\n".join(str(user_id) for user_id in config.security.allowed_telegram_user_ids),
            "categories_json": json.dumps(categories_payload),
            "success_message": success_message,
            "error_message": error_message,
            "backup_path": backup_path,
        },
    )


def _render_review_template(
    request: Request,
    review_queue: ReviewQueueService,
    batch_token: str | None,
    success_message: str | None,
    error_message: str | None,
) -> HTMLResponse:
    items = review_queue.get_batch_items(batch_token) if batch_token else review_queue.list_dashboard_items(limit=100)
    pending_items = [item for item in items if item["status"] in {"pending_review", "notified", "review_in_progress"}]
    pending_batches = review_queue.list_pending_batches(limit=20) if not batch_token else []
    recent_destinations = review_queue.list_recent_destinations(limit=8)
    favorite_destinations = review_queue.list_favorite_destinations(limit=10)
    category_catalog = _build_review_category_catalog(review_queue.config.categories)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="review_queue.html",
        context={
            "request": request,
            "batch_token": batch_token,
            "items": items,
            "pending_items": pending_items,
            "pending_batches": pending_batches,
            "recent_destinations": recent_destinations,
            "favorite_destinations": favorite_destinations,
            "destination_options": review_queue.build_destination_options(),
            "review_category_catalog_json": json.dumps(category_catalog),
            "success_message": success_message,
            "error_message": error_message,
            "total_items": len(items),
            "pending_count": len(pending_items),
        },
    )


def _resolve_review_destination(form) -> str:
    category_name = clean_name(str(form.get("category_name", "")).strip())
    selected_subfolder = str(form.get("selected_subfolder", "")).strip()
    create_mode = str(form.get("create_mode", "none")).strip()
    new_folder_parts = split_folder_input(str(form.get("new_folder_name", "")).strip())

    if not category_name:
        legacy_manual = str(form.get("destination_manual", "")).strip()
        if legacy_manual:
            return legacy_manual
        return str(form.get("destination_select", "")).strip()

    destination_parts = [category_name]
    selected_parts = [part.strip() for part in selected_subfolder.split("/") if part.strip()]

    if create_mode in {"child", "selected"}:
        if not new_folder_parts:
            raise ValueError("Enter a new folder name to create under the selected location.")
        destination_parts.extend(selected_parts)
        destination_parts.extend(new_folder_parts)
        return " / ".join(destination_parts)

    if create_mode == "root":
        if not new_folder_parts:
            raise ValueError("Enter a new folder name to create under the category root.")
        destination_parts.extend(new_folder_parts)
        return " / ".join(destination_parts)

    if selected_parts:
        destination_parts.extend(selected_parts)
    return " / ".join(destination_parts)


def _build_review_category_catalog(categories: list[CategoryConfig]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for category in categories:
        catalog.append(
            {
                "name": category.name,
                "path": "",
                "children": _build_review_tree_nodes(category.folders, []),
            }
        )
    return catalog


def _build_review_tree_nodes(
    nodes: list[FolderNode],
    prefix: list[str],
    ) -> list[dict[str, Any]]:
    tree_nodes: list[dict[str, Any]] = []
    for node in nodes:
        current_path = [*prefix, node.name]
        tree_nodes.append(
            {
                "name": node.name,
                "path": " / ".join(current_path),
                "children": _build_review_tree_nodes(node.folders, current_path),
            }
        )
    return tree_nodes


def _parse_allowed_user_ids(raw_value: str) -> list[int]:
    user_ids: list[int] = []
    for part in raw_value.replace(",", "\n").splitlines():
        trimmed = part.strip()
        if not trimmed:
            continue
        user_ids.append(int(trimmed))
    if not user_ids:
        raise ValueError("At least one allowed Telegram user ID is required.")
    return user_ids


def _build_config_from_form(form) -> AppConfig:
    categories = _parse_categories_json(form.get("categories_json", "[]"))
    allowed_ids = _parse_allowed_user_ids(str(form.get("allowed_user_ids", "")))

    return AppConfig(
        telegram_bot_token=str(form.get("telegram_bot_token", "")).strip(),
        server=ServerConfig(
            server_id=str(form.get("server_id", "")).strip(),
            server_name=str(form.get("server_name", "")).strip(),
        ),
        security=SecurityConfig(allowed_telegram_user_ids=allowed_ids),
        paths=PathsConfig(
            base_storage_path=str(form.get("base_storage_path", "")).strip(),
            incoming_temp_path=str(form.get("incoming_temp_path", "")).strip(),
            database_path=str(form.get("database_path", "")).strip(),
            syncthing_inbox_path=str(form.get("syncthing_inbox_path", "")).strip(),
            syncthing_processed_path=str(form.get("syncthing_processed_path", "")).strip(),
            review_thumbnail_path=(
                str(form.get("review_thumbnail_path", "data/review-thumbnails")).strip()
                or "data/review-thumbnails"
            ),
        ),
        behavior=BehaviorConfig(
            delete_telegram_message_after_save=str(form.get("delete_after_save", "true")).lower() == "true",
            duplicate_action=str(form.get("duplicate_action", "skip")).strip() or "skip",
            allow_new_folder=str(form.get("allow_new_folder", "")) == "on",
            keep_original_filename=str(form.get("keep_original_filename", "")) == "on",
        ),
        categories=categories,
        folder_config=FolderConfig(
            categories_file=str(form.get("categories_file", "categories.yaml")).strip() or "categories.yaml"
        ),
        logging=LoggingConfig(
            level=str(form.get("logging_level", "INFO")).strip().upper() or "INFO",
            file=str(form.get("logging_file", "logs/media_sorter.log")).strip() or "logs/media_sorter.log",
        ),
        webui=WebUIConfig(
            host=str(form.get("webui_host", "127.0.0.1")).strip() or "127.0.0.1",
            port=int(str(form.get("webui_port", "8080")).strip() or "8080"),
        ),
        review_queue=ReviewQueueConfig(
            enabled=str(form.get("review_queue_enabled", "")) == "on",
            poll_interval_seconds=int(str(form.get("review_queue_poll_interval_seconds", "15")).strip() or "15"),
            stable_file_age_seconds=int(
                str(form.get("review_queue_stable_file_age_seconds", "30")).strip() or "30"
            ),
            notification_batch_minutes=int(
                str(form.get("review_queue_notification_batch_minutes", "5")).strip() or "5"
            ),
            batch_size_default=int(str(form.get("review_queue_batch_size_default", "15")).strip() or "15"),
            review_base_url=str(form.get("review_queue_review_base_url", "")).strip(),
            link_secret=str(form.get("review_queue_link_secret", "")).strip(),
            delete_inbox_file_after_save=str(form.get("review_queue_delete_inbox_file_after_save", "")) == "on",
            generate_image_thumbnails=str(form.get("review_queue_generate_image_thumbnails", "")) == "on",
            generate_video_thumbnails=str(form.get("review_queue_generate_video_thumbnails", "")) == "on",
            generate_pdf_previews=str(form.get("review_queue_generate_pdf_previews", "")) == "on",
        ),
        local_bot_api=LocalBotAPIConfig(
            enabled=str(form.get("local_bot_api_enabled", "")) == "on",
            auto_start=str(form.get("local_bot_api_auto_start", "")) == "on",
            base_url=str(form.get("local_bot_api_base_url", "http://127.0.0.1:8081/bot")).strip()
            or "http://127.0.0.1:8081/bot",
            base_file_url=(
                str(form.get("local_bot_api_base_file_url", "http://127.0.0.1:8081/file/bot")).strip()
                or "http://127.0.0.1:8081/file/bot"
            ),
            http_host=str(form.get("local_bot_api_http_host", "127.0.0.1")).strip() or "127.0.0.1",
            http_port=int(str(form.get("local_bot_api_http_port", "8081")).strip() or "8081"),
            binary_path=str(form.get("local_bot_api_binary_path", "telegram-bot-api.exe")).strip()
            or "telegram-bot-api.exe",
            working_dir=str(form.get("local_bot_api_working_dir", "data/telegram-bot-api")).strip()
            or "data/telegram-bot-api",
            temp_dir=str(form.get("local_bot_api_temp_dir", "data/telegram-bot-api/tmp")).strip()
            or "data/telegram-bot-api/tmp",
            log_file=str(form.get("local_bot_api_log_file", "logs/telegram-bot-api.log")).strip()
            or "logs/telegram-bot-api.log",
        ),
    )


def _parse_categories_json(raw_json: str) -> list[CategoryConfig]:
    values = json.loads(raw_json or "[]")
    if not isinstance(values, list):
        raise ValueError("categories must be a list.")

    categories: list[CategoryConfig] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Each category must be an object.")

        raw_name = str(value.get("name", "")).strip()
        if not raw_name:
            continue
        name = clean_name(raw_name)
        if name in seen:
            continue

        seen.add(name)
        folders = _parse_folder_nodes(value.get("folders", []), field_name=f"categories[{name}].folders")
        categories.append(
            CategoryConfig(
                name=name,
                root_path=str(value.get("root_path", "")).strip(),
                folders=folders,
            )
        )

    if not categories:
        raise ValueError("At least one category is required.")

    return categories


def _parse_folder_nodes(raw_nodes: object, field_name: str) -> list[FolderNode]:
    if raw_nodes is None:
        return []
    if not isinstance(raw_nodes, list):
        raise ValueError(f"{field_name} must be a list.")

    folders: list[FolderNode] = []
    seen: set[str] = set()
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict):
            raise ValueError(f"{field_name} entries must be objects.")

        raw_name = str(raw_node.get("name", "")).strip()
        if not raw_name:
            continue
        name = clean_name(raw_name)
        if name in seen:
            continue

        seen.add(name)
        folders.append(
            FolderNode(
                name=name,
                folders=_parse_folder_nodes(
                    raw_node.get("folders", []),
                    field_name=f"{field_name}[{name}].folders",
                ),
            )
        )

    return folders


def _parse_selected_categories_json(raw_json: object) -> list[str]:
    values = json.loads(str(raw_json or "[]"))
    if not isinstance(values, list):
        raise ValueError("Selected categories must be a list.")

    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        raw_name = str(value).strip()
        if not raw_name:
            continue
        name = clean_name(raw_name)
        if name in seen:
            continue
        seen.add(name)
        selected.append(name)
    return selected


def _validate_config(config: AppConfig) -> None:
    if not config.telegram_bot_token:
        raise ValueError("Telegram bot token is required.")
    if not config.server.server_id or not config.server.server_name:
        raise ValueError("server_id and server_name are required.")
    if not config.paths.incoming_temp_path or not config.paths.database_path:
        raise ValueError("incoming_temp_path and database_path are required.")
    if not config.paths.base_storage_path and not any(category.root_path.strip() for category in config.categories):
        raise ValueError("Set either a global base_storage_path or at least one category root_path.")
    if config.behavior.duplicate_action not in {"skip", "ask", "save_anyway"}:
        raise ValueError("duplicate_action must be one of: skip, ask, save_anyway.")
    if config.local_bot_api.enabled:
        if not config.local_bot_api.base_url or not config.local_bot_api.base_file_url:
            raise ValueError("local Bot API base_url and base_file_url are required when enabled.")
        if not config.local_bot_api.binary_path:
            raise ValueError("local Bot API binary_path is required when enabled.")
        if config.local_bot_api.http_port < 1 or config.local_bot_api.http_port > 65535:
            raise ValueError("local Bot API http_port must be between 1 and 65535.")
    if config.review_queue.enabled:
        if not config.paths.syncthing_inbox_path:
            raise ValueError("syncthing_inbox_path is required when Syncthing review mode is enabled.")
        if config.review_queue.poll_interval_seconds < 5:
            raise ValueError("review_queue poll_interval_seconds must be at least 5.")
        if config.review_queue.stable_file_age_seconds < 5:
            raise ValueError("review_queue stable_file_age_seconds must be at least 5.")
        if config.review_queue.notification_batch_minutes < 1:
            raise ValueError("review_queue notification_batch_minutes must be at least 1.")
        if config.review_queue.batch_size_default < 1:
            raise ValueError("review_queue batch_size_default must be at least 1.")


def _create_missing_folders(config: AppConfig) -> None:
    if config.paths.base_storage_path:
        ensure_directory(Path(config.paths.base_storage_path))
    ensure_directory(Path(config.paths.incoming_temp_path))
    ensure_directory(Path(config.paths.database_path).parent)
    ensure_directory(Path(config.paths.review_thumbnail_path))
    ensure_directory(Path(config.logging.file).parent)
    ensure_directory(Path(config.local_bot_api.working_dir))
    ensure_directory(Path(config.local_bot_api.temp_dir))
    ensure_directory(Path(config.local_bot_api.log_file).parent)

    if config.paths.syncthing_inbox_path:
        ensure_directory(Path(config.paths.syncthing_inbox_path))
    if config.paths.syncthing_processed_path:
        ensure_directory(Path(config.paths.syncthing_processed_path))

    for category in config.categories:
        category_root = config.get_category_root_path(category.name)
        if category_root is None:
            raise ValueError(
                f"Category '{category.name}' does not have a root_path and no global base_storage_path is configured."
            )
        category_root = ensure_directory(category_root)
        _create_folder_nodes(category_root, category.folders)


def _create_folder_nodes(parent: Path, folders: list[FolderNode]) -> None:
    for folder in folders:
        child = ensure_directory(parent / folder.name)
        _create_folder_nodes(child, folder.folders)


def _merge_selected_categories_from_disk(config: AppConfig, selected_categories: list[str]) -> tuple[int, list[str]]:
    imported_nodes = 0
    merged_categories: list[str] = []

    for category_name in selected_categories:
        category = config.get_category(category_name)
        if not category:
            continue

        merged_categories.append(category_name)
        category_root = config.get_category_root_path(category_name)
        if category_root is None:
            raise ValueError(
                f"Category '{category_name}' does not have a root_path and no global base_storage_path is configured."
            )
        discovered = _scan_folder_nodes_from_disk(category_root)
        imported_nodes += _merge_folder_nodes(category.folders, discovered)

    if not merged_categories:
        raise ValueError("None of the selected categories exist in the current configuration.")

    return imported_nodes, merged_categories


def _scan_folder_nodes_from_disk(root: Path) -> list[FolderNode]:
    if not root.exists() or not root.is_dir():
        return []

    child_directories = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.name.lower(),
    )
    return [
        FolderNode(
            name=clean_name(child.name),
            folders=_scan_folder_nodes_from_disk(child),
        )
        for child in child_directories
    ]


def _merge_folder_nodes(existing: list[FolderNode], discovered: list[FolderNode]) -> int:
    imported_count = 0
    existing_map = {node.name: node for node in existing}

    for discovered_node in discovered:
        existing_node = existing_map.get(discovered_node.name)
        if existing_node is None:
            existing.append(discovered_node)
            existing_map[discovered_node.name] = discovered_node
            imported_count += 1 + _count_folder_nodes(discovered_node.folders)
            continue

        imported_count += _merge_folder_nodes(existing_node.folders, discovered_node.folders)

    return imported_count


def _count_folder_nodes(nodes: list[FolderNode]) -> int:
    return sum(1 + _count_folder_nodes(node.folders) for node in nodes)
