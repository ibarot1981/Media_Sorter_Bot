from __future__ import annotations

import json
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from pathlib import Path
import secrets
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

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
    normalize_trusted_networks,
    save_config,
)
from src.database import Database
from src.duplicates import DuplicateChecker
from src.review_queue import ReviewQueueService
from src.review_jobs import ReviewJobStore
from src.storage import StorageService
from src.utils import clean_name, ensure_directory, split_folder_input


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class ReviewMoveRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=500)
    destination: str = Field(min_length=1, max_length=1000)


def _is_trusted_client(
    client_host: str | None,
    trusted_networks: tuple,
) -> bool:
    if not client_host:
        return False
    try:
        address: IPv4Address | IPv6Address = ip_address(client_host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address.version == network.version and address in network for network in trusted_networks)


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
    review_jobs = ReviewJobStore()

    app = FastAPI(title="Media Sorter Bot Config Editor")
    app.state.csrf_token = secrets.token_urlsafe(32)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    trusted_networks = tuple(ip_network(value, strict=False) for value in config.webui.trusted_networks)

    @app.middleware("http")
    async def restrict_to_trusted_networks(request: Request, call_next):
        client_host = request.client.host if request.client else None
        if not _is_trusted_client(client_host, trusted_networks):
            return PlainTextResponse("Web UI access is restricted to trusted private networks.", status_code=403)
        return await call_next(request)

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
                success_message += f" {result['failed_count']} file(s) failed and remain pending for retry."
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
                success_message += f" {result['failed_count']} file(s) failed and remain pending for retry."
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

    @app.get("/review/media/{item_id}")
    async def review_media(item_id: int):
        item = review_queue.database.get_pending_item(item_id)
        if not item or str(item.get("status")) not in {"pending_review", "notified", "review_in_progress"}:
            return HTMLResponse(status_code=404, content="Item not found.")
        mime_type = str(item.get("mime_type", "") or "")
        if not mime_type.startswith("image/"):
            return HTMLResponse(status_code=415, content="A full preview is only available for images.")
        source_path = Path(str(item.get("source_path", "") or ""))
        if not source_path.is_file():
            return HTMLResponse(status_code=404, content="Source image not found.")
        return FileResponse(source_path, media_type=mime_type)

    @app.get("/api/v1/review/workspace")
    async def review_workspace():
        review_queue.refresh_runtime_config()
        items = review_queue.list_dashboard_items(limit=500, refresh_config=False)
        batches = review_queue.list_pending_batches(limit=100, refresh_config=False)
        return {
            "items": [_serialize_review_item(item) for item in items],
            "batches": batches,
            "categories": _build_review_category_roots(review_queue.config.categories),
            "recent_destinations": review_queue.list_recent_destinations(limit=8, refresh_config=False),
            "favorite_destinations": review_queue.list_favorite_destinations(limit=12, refresh_config=False),
            "processing_item_ids": review_jobs.reserved_item_ids(),
        }

    @app.get("/api/v1/review/destinations")
    async def review_destinations(category: str = "", parent: str = "", q: str = ""):
        if len(category) > 200 or len(parent) > 1000 or len(q) > 200:
            raise HTTPException(status_code=400, detail="Destination query is too long.")
        if q.strip():
            matches, truncated = _search_review_destinations(review_queue.config.categories, q, limit=100)
            return {"items": matches, "truncated": truncated}
        if not category.strip():
            return {"items": _build_review_category_roots(review_queue.config.categories), "truncated": False}
        children = _get_review_destination_children(review_queue.config.categories, category, parent)
        if children is None:
            raise HTTPException(status_code=404, detail="Destination folder not found.")
        return {"items": children, "truncated": False}

    @app.post("/api/v1/review/move", status_code=202)
    async def start_review_move(
        payload: ReviewMoveRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        _require_csrf(request)
        item_ids = list(dict.fromkeys(payload.item_ids))
        missing_or_completed = []
        for item_id in item_ids:
            item = review_queue.database.get_pending_item(item_id)
            if not item or str(item.get("status")) not in {"pending_review", "notified", "review_in_progress"}:
                missing_or_completed.append(item_id)
        if missing_or_completed:
            raise HTTPException(status_code=409, detail="Some selected files are no longer available for review.")

        category_name, folder_path = review_queue.parse_destination_value(payload.destination)
        destination = " / ".join([category_name, *folder_path])
        try:
            job = review_jobs.create_move_job(item_ids, destination)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        background_tasks.add_task(
            _execute_review_move_job,
            review_jobs,
            str(job["id"]),
            review_queue,
            item_ids,
            destination,
        )
        return job

    @app.get("/api/v1/review/jobs/{job_id}")
    async def review_job(job_id: str):
        job = review_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Review job not found.")
        return job

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
    return TEMPLATES.TemplateResponse(
        request=request,
        name="review_queue.html",
        context={
            "request": request,
            "batch_token": batch_token,
            "csrf_token": request.app.state.csrf_token,
            "success_message": success_message,
            "error_message": error_message,
        },
    )


def _require_csrf(request: Request) -> None:
    supplied_token = request.headers.get("X-CSRF-Token", "")
    expected_token = str(request.app.state.csrf_token)
    if not supplied_token or not secrets.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


def _execute_review_move_job(
    jobs: ReviewJobStore,
    job_id: str,
    review_queue: ReviewQueueService,
    item_ids: list[int],
    destination: str,
) -> None:
    jobs.run(job_id, lambda: review_queue.save_items(item_ids, destination))


def _serialize_review_item(item: dict[str, Any]) -> dict[str, Any]:
    mime_type = str(item.get("mime_type", "") or "")
    return {
        "id": int(item["id"]),
        "original_file_name": str(item.get("original_file_name", "")),
        "batch_token": str(item.get("batch_token", "") or ""),
        "status": str(item.get("status", "")),
        "source_label": str(item.get("source_label", "") or "Inbox"),
        "source_relative_path": str(item.get("source_relative_path", "") or ""),
        "mime_type": mime_type,
        "file_label": str(item.get("file_label", "FILE")),
        "thumbnail_url": str(item.get("thumbnail_url", "") or ""),
        "media_url": f"/review/media/{int(item['id'])}" if mime_type.startswith("image/") else "",
        "display_size_mb": item.get("display_size_mb", 0),
        "width": item.get("width"),
        "height": item.get("height"),
        "display_duration": str(item.get("display_duration", "") or ""),
        "display_page_count": item.get("display_page_count", 0),
        "received_at": str(item.get("received_at", "") or ""),
        "error_message": str(item.get("error_message", "") or ""),
    }


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


def _build_review_category_roots(categories: list[CategoryConfig]) -> list[dict[str, Any]]:
    return [
        {"name": category.name, "path": "", "has_children": bool(category.folders)}
        for category in categories
    ]


def _get_review_destination_children(
    categories: list[CategoryConfig],
    category_name: str,
    parent_path: str,
) -> list[dict[str, Any]] | None:
    category = next((item for item in categories if item.name == category_name), None)
    if category is None:
        return None

    nodes = category.folders
    path_parts = [part.strip() for part in parent_path.split("/") if part.strip()]
    for part in path_parts:
        match = next((node for node in nodes if node.name == part), None)
        if match is None:
            return None
        nodes = match.folders

    prefix = " / ".join(path_parts)
    return [
        {
            "name": node.name,
            "path": " / ".join(part for part in (prefix, node.name) if part),
            "has_children": bool(node.folders),
        }
        for node in nodes
    ]


def _search_review_destinations(
    categories: list[CategoryConfig],
    query: str,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    normalized_query = query.strip().casefold()
    matches: list[dict[str, Any]] = []
    truncated = False

    def add(value: str, label: str, depth: int, has_children: bool) -> None:
        nonlocal truncated
        if normalized_query not in value.casefold():
            return
        if len(matches) >= limit:
            truncated = True
            return
        matches.append(
            {
                "name": label,
                "label": value,
                "value": value,
                "depth": depth,
                "has_children": has_children,
            }
        )

    def visit(category_name: str, nodes: list[FolderNode], prefix: list[str]) -> None:
        for node in nodes:
            current_path = [*prefix, node.name]
            value = " / ".join([category_name, *current_path])
            add(value, node.name, len(current_path), bool(node.folders))
            visit(category_name, node.folders, current_path)

    for category in categories:
        add(category.name, category.name, 0, bool(category.folders))
        visit(category.name, category.folders, [])
    return matches, truncated


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
            trusted_networks=normalize_trusted_networks(
                [
                    line.strip()
                    for line in str(form.get("webui_trusted_networks", "")).replace(",", "\n").splitlines()
                    if line.strip()
                ]
            ),
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
    normalize_trusted_networks(config.webui.trusted_networks)
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
