from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import threading
from typing import Any

import yaml

from src.utils import clean_name, dedupe_preserve_order


_CONFIG_IO_LOCK = threading.RLock()


@dataclass(slots=True)
class FolderNode:
    name: str
    folders: list["FolderNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name}
        if self.folders:
            payload["folders"] = [folder.to_dict() for folder in self.folders]
        return payload


@dataclass(slots=True)
class CategoryConfig:
    name: str
    root_path: str = ""
    folders: list[FolderNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name}
        if self.root_path:
            payload["root_path"] = self.root_path
        if self.folders:
            payload["folders"] = [folder.to_dict() for folder in self.folders]
        return payload


@dataclass(slots=True)
class FolderConfig:
    categories_file: str = "categories.yaml"


@dataclass(slots=True)
class ServerConfig:
    server_id: str
    server_name: str


@dataclass(slots=True)
class SecurityConfig:
    allowed_telegram_user_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class PathsConfig:
    base_storage_path: str
    incoming_temp_path: str
    database_path: str
    syncthing_inbox_path: str = ""
    syncthing_processed_path: str = ""
    review_thumbnail_path: str = "data/review-thumbnails"


@dataclass(slots=True)
class BehaviorConfig:
    delete_telegram_message_after_save: bool = True
    duplicate_action: str = "skip"
    allow_new_folder: bool = True
    keep_original_filename: bool = True


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/media_sorter.log"


@dataclass(slots=True)
class WebUIConfig:
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass(slots=True)
class ReviewQueueConfig:
    enabled: bool = False
    poll_interval_seconds: int = 15
    stable_file_age_seconds: int = 30
    notification_batch_minutes: int = 5
    batch_size_default: int = 15
    review_base_url: str = ""
    link_secret: str = ""
    delete_inbox_file_after_save: bool = True
    generate_image_thumbnails: bool = True
    generate_video_thumbnails: bool = True
    generate_pdf_previews: bool = True


@dataclass(slots=True)
class LocalBotAPIConfig:
    enabled: bool = False
    auto_start: bool = True
    base_url: str = "http://127.0.0.1:8081/bot"
    base_file_url: str = "http://127.0.0.1:8081/file/bot"
    http_host: str = "127.0.0.1"
    http_port: int = 8081
    binary_path: str = "telegram-bot-api.exe"
    working_dir: str = "data/telegram-bot-api"
    temp_dir: str = "data/telegram-bot-api/tmp"
    log_file: str = "logs/telegram-bot-api.log"


@dataclass(slots=True)
class AppConfig:
    telegram_bot_token: str
    server: ServerConfig
    security: SecurityConfig
    paths: PathsConfig
    behavior: BehaviorConfig
    categories: list[CategoryConfig]
    folder_config: FolderConfig = field(default_factory=FolderConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    webui: WebUIConfig = field(default_factory=WebUIConfig)
    review_queue: ReviewQueueConfig = field(default_factory=ReviewQueueConfig)
    local_bot_api: LocalBotAPIConfig = field(default_factory=LocalBotAPIConfig)

    def to_dict(self, *, include_categories: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if include_categories:
            payload["categories"] = [category.to_dict() for category in self.categories]
        else:
            payload.pop("categories", None)
        return payload

    def get_category(self, category_name: str) -> CategoryConfig | None:
        for category in self.categories:
            if category.name == category_name:
                return category
        return None

    def ensure_folder_path(self, category_name: str, folder_path: list[str] | tuple[str, ...]) -> bool:
        category = self.get_category(category_name)
        if not category:
            return False

        changed = False
        nodes = category.folders
        for folder_name in folder_path:
            cleaned_name = clean_name(folder_name)
            existing = next((node for node in nodes if node.name == cleaned_name), None)
            if existing is None:
                existing = FolderNode(name=cleaned_name)
                nodes.append(existing)
                changed = True
            nodes = existing.folders
        return changed

    def get_category_root_path(self, category_name: str) -> Path | None:
        category = self.get_category(category_name)
        if not category:
            return None
        if category.root_path.strip():
            return Path(category.root_path.strip())
        if self.paths.base_storage_path.strip():
            return Path(self.paths.base_storage_path.strip()) / clean_name(category.name)
        return None

    @property
    def category_names(self) -> list[str]:
        return [category.name for category in self.categories]


def _normalize_category_list(values: list[Any]) -> list[CategoryConfig]:
    categories: list[CategoryConfig] = []
    seen: set[str] = set()

    for value in values:
        if isinstance(value, dict):
            raw_name = str(value.get("name", "")).strip()
            raw_root_path = str(value.get("root_path", "")).strip()
            raw_folders = value.get("folders", [])
        else:
            raw_name = str(value).strip()
            raw_root_path = ""
            raw_folders = []

        if not raw_name:
            continue

        name = clean_name(raw_name)
        if name in seen:
            continue

        seen.add(name)
        categories.append(
            CategoryConfig(
                name=name,
                root_path=raw_root_path,
                folders=_normalize_folder_nodes(raw_folders, field_name=f"categories[{name}].folders"),
            )
        )

    if not categories:
        raise ValueError("categories must contain at least one entry.")

    return categories


def _normalize_folder_nodes(values: Any, field_name: str) -> list[FolderNode]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list.")

    nodes: list[FolderNode] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            raw_name = str(value.get("name", "")).strip()
            raw_children = value.get("folders", [])
        else:
            raw_name = str(value).strip()
            raw_children = []

        if not raw_name:
            continue

        name = clean_name(raw_name)
        if name in seen:
            continue

        seen.add(name)
        nodes.append(
            FolderNode(
                name=name,
                folders=_normalize_folder_nodes(raw_children, field_name=f"{field_name}[{name}].folders"),
            )
        )

    return nodes


def _load_legacy_category_list(raw_data: dict[str, Any]) -> list[CategoryConfig]:
    raw_categories = raw_data.get("categories", [])
    category_names = _normalize_named_list(raw_categories, "categories")
    products = _normalize_named_list(raw_data.get("products", []), "products") if raw_data.get("products") else []
    folder_map = raw_data.get("folder_map", {})

    categories: list[CategoryConfig] = []
    for category_name in category_names:
        folders: list[FolderNode] = []
        for product_name in products:
            subfolder_values: list[Any] = []
            if isinstance(folder_map, dict):
                category_map = folder_map.get(category_name, {})
                if isinstance(category_map, dict):
                    raw_subfolders = category_map.get(product_name, [])
                    if isinstance(raw_subfolders, list):
                        subfolder_values = raw_subfolders
                    elif isinstance(raw_subfolders, str):
                        subfolder_values = [raw_subfolders]

            folders.append(
                FolderNode(
                    name=product_name,
                    folders=_normalize_folder_nodes(
                        [{"name": str(value).strip()} for value in subfolder_values if str(value).strip()],
                        field_name=f"folder_map[{category_name}][{product_name}]",
                    ),
                )
            )
        categories.append(CategoryConfig(name=category_name, root_path="", folders=folders))

    return categories


def _normalize_named_list(values: list[Any], field_name: str) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            name = str(value.get("name", "")).strip()
        else:
            name = str(value).strip()
        if not name:
            continue
        result.append(clean_name(name))
    unique_values = dedupe_preserve_order(result)
    if not unique_values:
        raise ValueError(f"{field_name} must contain at least one entry.")
    return unique_values


def _read_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _resolve_categories_file(config_path: Path, folder_config: FolderConfig) -> Path:
    raw_path = folder_config.categories_file.strip() or "categories.yaml"
    categories_path = Path(raw_path)
    if not categories_path.is_absolute():
        categories_path = (config_path.parent / categories_path).resolve()
    return categories_path


def _load_categories(config_path: Path, raw_data: dict[str, Any], folder_config: FolderConfig) -> list[CategoryConfig]:
    categories_path = _resolve_categories_file(config_path, folder_config)
    if categories_path.exists():
        categories_data = _read_yaml_file(categories_path)
        raw_categories = categories_data.get("categories", [])
    else:
        raw_categories = raw_data.get("categories", [])
        if folder_config.categories_file and not raw_categories:
            raise ValueError(f"Categories file was not found: {categories_path}")

    if not isinstance(raw_categories, list):
        raise ValueError("categories must be a list.")

    uses_nested_categories = raw_categories and all(isinstance(value, dict) for value in raw_categories) and (
        any(isinstance(value, dict) and "folders" in value for value in raw_categories)
        or ("products" not in raw_data and "folder_map" not in raw_data)
    )

    if uses_nested_categories:
        return _normalize_category_list(raw_categories)
    return _load_legacy_category_list(raw_data)


def load_config(config_path: Path) -> AppConfig:
    with _CONFIG_IO_LOCK:
        config_path = config_path.resolve()
        raw_data = _read_yaml_file(config_path)

    telegram_bot_token = str(raw_data.get("telegram_bot_token", "")).strip()
    if not telegram_bot_token:
        raise ValueError("telegram_bot_token is required in config.yaml.")

    server_raw = raw_data.get("server", {})
    server = ServerConfig(
        server_id=str(server_raw.get("server_id", "")).strip(),
        server_name=str(server_raw.get("server_name", "")).strip(),
    )
    if not server.server_id or not server.server_name:
        raise ValueError("server.server_id and server.server_name are required.")

    security_raw = raw_data.get("security", {})
    allowed_ids = security_raw.get("allowed_telegram_user_ids", [])
    if not isinstance(allowed_ids, list):
        raise ValueError("security.allowed_telegram_user_ids must be a list.")
    security = SecurityConfig(allowed_telegram_user_ids=[int(value) for value in allowed_ids])

    paths_raw = raw_data.get("paths", {})
    paths = PathsConfig(
        base_storage_path=str(paths_raw.get("base_storage_path", "")).strip(),
        incoming_temp_path=str(paths_raw.get("incoming_temp_path", "")).strip(),
        database_path=str(paths_raw.get("database_path", "")).strip(),
        syncthing_inbox_path=str(paths_raw.get("syncthing_inbox_path", "")).strip(),
        syncthing_processed_path=str(paths_raw.get("syncthing_processed_path", "")).strip(),
        review_thumbnail_path=(
            str(paths_raw.get("review_thumbnail_path", "data/review-thumbnails")).strip()
            or "data/review-thumbnails"
        ),
    )
    if not paths.incoming_temp_path or not paths.database_path:
        raise ValueError("incoming_temp_path and database_path are required.")

    behavior_raw = raw_data.get("behavior", {})
    behavior = BehaviorConfig(
        delete_telegram_message_after_save=bool(behavior_raw.get("delete_telegram_message_after_save", True)),
        duplicate_action=str(behavior_raw.get("duplicate_action", "skip")).strip() or "skip",
        allow_new_folder=bool(behavior_raw.get("allow_new_folder", True)),
        keep_original_filename=bool(behavior_raw.get("keep_original_filename", True)),
    )

    folder_config_raw = raw_data.get("folder_config", {})
    folder_config = FolderConfig(
        categories_file=str(folder_config_raw.get("categories_file", "categories.yaml")).strip() or "categories.yaml"
    )
    categories = _load_categories(config_path, raw_data, folder_config)

    logging_raw = raw_data.get("logging", {})
    logging_config = LoggingConfig(
        level=str(logging_raw.get("level", "INFO")).strip().upper() or "INFO",
        file=str(logging_raw.get("file", "logs/media_sorter.log")).strip() or "logs/media_sorter.log",
    )

    webui_raw = raw_data.get("webui", {})
    webui = WebUIConfig(
        host=str(webui_raw.get("host", "127.0.0.1")).strip() or "127.0.0.1",
        port=int(webui_raw.get("port", 8080)),
    )

    review_queue_raw = raw_data.get("review_queue", {})
    review_queue = ReviewQueueConfig(
        enabled=bool(review_queue_raw.get("enabled", False)),
        poll_interval_seconds=max(5, int(review_queue_raw.get("poll_interval_seconds", 15))),
        stable_file_age_seconds=max(5, int(review_queue_raw.get("stable_file_age_seconds", 30))),
        notification_batch_minutes=max(1, int(review_queue_raw.get("notification_batch_minutes", 5))),
        batch_size_default=max(1, int(review_queue_raw.get("batch_size_default", 15))),
        review_base_url=str(review_queue_raw.get("review_base_url", "")).strip(),
        link_secret=str(review_queue_raw.get("link_secret", "")).strip(),
        delete_inbox_file_after_save=bool(review_queue_raw.get("delete_inbox_file_after_save", True)),
        generate_image_thumbnails=bool(review_queue_raw.get("generate_image_thumbnails", True)),
        generate_video_thumbnails=bool(review_queue_raw.get("generate_video_thumbnails", True)),
        generate_pdf_previews=bool(review_queue_raw.get("generate_pdf_previews", True)),
    )

    local_bot_api_raw = raw_data.get("local_bot_api", {})
    local_bot_api = LocalBotAPIConfig(
        enabled=bool(local_bot_api_raw.get("enabled", False)),
        auto_start=bool(local_bot_api_raw.get("auto_start", True)),
        base_url=(
            str(local_bot_api_raw.get("base_url", "http://127.0.0.1:8081/bot")).strip()
            or "http://127.0.0.1:8081/bot"
        ),
        base_file_url=(
            str(local_bot_api_raw.get("base_file_url", "http://127.0.0.1:8081/file/bot")).strip()
            or "http://127.0.0.1:8081/file/bot"
        ),
        http_host=str(local_bot_api_raw.get("http_host", "127.0.0.1")).strip() or "127.0.0.1",
        http_port=int(local_bot_api_raw.get("http_port", 8081)),
        binary_path=str(local_bot_api_raw.get("binary_path", "telegram-bot-api.exe")).strip()
        or "telegram-bot-api.exe",
        working_dir=(
            str(local_bot_api_raw.get("working_dir", "data/telegram-bot-api")).strip()
            or "data/telegram-bot-api"
        ),
        temp_dir=(
            str(local_bot_api_raw.get("temp_dir", "data/telegram-bot-api/tmp")).strip()
            or "data/telegram-bot-api/tmp"
        ),
        log_file=str(local_bot_api_raw.get("log_file", "logs/telegram-bot-api.log")).strip()
        or "logs/telegram-bot-api.log",
    )

    return AppConfig(
        telegram_bot_token=telegram_bot_token,
        server=server,
        security=security,
        paths=paths,
        behavior=behavior,
        categories=categories,
        folder_config=folder_config,
        logging=logging_config,
        webui=webui,
        review_queue=review_queue,
        local_bot_api=local_bot_api,
    )


def _write_categories_file(config: AppConfig, config_path: Path) -> Path:
    categories_path = _resolve_categories_file(config_path, config.folder_config)
    categories_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"categories": [category.to_dict() for category in config.categories]}
    temp_path = categories_path.with_name(f"{categories_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=False)
    temp_path.replace(categories_path)
    return categories_path


def save_config(config: AppConfig, config_path: Path, create_backup: bool = True) -> Path | None:
    with _CONFIG_IO_LOCK:
        config_path = config_path.resolve()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path: Path | None = None

        if create_backup and config_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = config_path.with_name(f"config.backup.{timestamp}.yaml")
            backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

            categories_path = _resolve_categories_file(config_path, config.folder_config)
            if categories_path.exists():
                categories_backup = categories_path.with_name(f"{categories_path.stem}.backup.{timestamp}{categories_path.suffix}")
                categories_backup.write_text(categories_path.read_text(encoding="utf-8"), encoding="utf-8")

        main_payload = config.to_dict(include_categories=False)
        temp_path = config_path.with_name(f"{config_path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(main_payload, handle, sort_keys=False, allow_unicode=False)
        temp_path.replace(config_path)

        _write_categories_file(config, config_path)
        return backup_path
