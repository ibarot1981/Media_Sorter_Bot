from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import (
    AppConfig,
    BehaviorConfig,
    CategoryConfig,
    FolderNode,
    LoggingConfig,
    PathsConfig,
    SecurityConfig,
    ServerConfig,
    WebUIConfig,
    load_config,
    save_config,
)
from src.utils import clean_name, ensure_directory


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_web_app(config_path: Path) -> FastAPI:
    app = FastAPI(title="Media Sorter Bot Config Editor")
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        config = load_config(config_path)
        return _render_template(request, config, success_message=None, error_message=None, backup_path=None)

    @app.post("/save", response_class=HTMLResponse)
    async def save(request: Request) -> HTMLResponse:
        form = await request.form()

        try:
            config = _build_config_from_form(form)
            _validate_config(config)
            backup_path = save_config(config, config_path, create_backup=True)

            if str(form.get("create_missing_folders", "")) == "on":
                _create_missing_folders(config)

            return _render_template(
                request,
                config,
                success_message="Configuration saved successfully.",
                error_message=None,
                backup_path=str(backup_path) if backup_path else None,
            )
        except Exception as exc:
            current_config = load_config(config_path)
            return _render_template(
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
            config = _build_config_from_form(form)
            _validate_config(config)

            selected_categories = _parse_selected_categories_json(form.get("import_categories_json", "[]"))
            if not selected_categories:
                raise ValueError("Select at least one category to import from disk.")

            imported_nodes, scanned_categories = _merge_selected_categories_from_disk(config, selected_categories)
            backup_path = save_config(config, config_path, create_backup=True)

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

            return _render_template(
                request,
                config,
                success_message=success_message,
                error_message=None,
                backup_path=str(backup_path) if backup_path else None,
            )
        except Exception as exc:
            current_config = load_config(config_path)
            return _render_template(
                request,
                current_config,
                success_message=None,
                error_message=str(exc),
                backup_path=None,
            )

    return app


def _render_template(
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
        ),
        behavior=BehaviorConfig(
            delete_telegram_message_after_save=str(form.get("delete_after_save", "true")).lower() == "true",
            duplicate_action=str(form.get("duplicate_action", "skip")).strip() or "skip",
            allow_new_folder=str(form.get("allow_new_folder", "")) == "on",
            keep_original_filename=str(form.get("keep_original_filename", "")) == "on",
        ),
        categories=categories,
        logging=LoggingConfig(
            level=str(form.get("logging_level", "INFO")).strip().upper() or "INFO",
            file=str(form.get("logging_file", "logs/media_sorter.log")).strip() or "logs/media_sorter.log",
        ),
        webui=WebUIConfig(
            host=str(form.get("webui_host", "127.0.0.1")).strip() or "127.0.0.1",
            port=int(str(form.get("webui_port", "8080")).strip() or "8080"),
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
        categories.append(CategoryConfig(name=name, folders=folders))

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
    if not config.paths.base_storage_path or not config.paths.incoming_temp_path or not config.paths.database_path:
        raise ValueError("All path fields are required.")
    if config.behavior.duplicate_action not in {"skip", "ask", "save_anyway"}:
        raise ValueError("duplicate_action must be one of: skip, ask, save_anyway.")


def _create_missing_folders(config: AppConfig) -> None:
    base_path = Path(config.paths.base_storage_path)
    ensure_directory(base_path)
    ensure_directory(Path(config.paths.incoming_temp_path))
    ensure_directory(Path(config.paths.database_path).parent)
    ensure_directory(Path(config.logging.file).parent)

    for category in config.categories:
        category_root = ensure_directory(base_path / category.name)
        _create_folder_nodes(category_root, category.folders)


def _create_folder_nodes(parent: Path, folders: list[FolderNode]) -> None:
    for folder in folders:
        child = ensure_directory(parent / folder.name)
        _create_folder_nodes(child, folder.folders)


def _merge_selected_categories_from_disk(config: AppConfig, selected_categories: list[str]) -> tuple[int, list[str]]:
    imported_nodes = 0
    merged_categories: list[str] = []
    base_path = Path(config.paths.base_storage_path)

    for category_name in selected_categories:
        category = config.get_category(category_name)
        if not category:
            continue

        merged_categories.append(category_name)
        discovered = _scan_folder_nodes_from_disk(base_path / category.name)
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
