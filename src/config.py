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
    folders: list[FolderNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name}
        if self.folders:
            payload["folders"] = [folder.to_dict() for folder in self.folders]
        return payload


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
class AppConfig:
    telegram_bot_token: str
    server: ServerConfig
    security: SecurityConfig
    paths: PathsConfig
    behavior: BehaviorConfig
    categories: list[CategoryConfig]
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    webui: WebUIConfig = field(default_factory=WebUIConfig)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["categories"] = [category.to_dict() for category in self.categories]
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

    @property
    def category_names(self) -> list[str]:
        return [category.name for category in self.categories]


def _normalize_category_list(values: list[Any]) -> list[CategoryConfig]:
    categories: list[CategoryConfig] = []
    seen: set[str] = set()

    for value in values:
        if isinstance(value, dict):
            raw_name = str(value.get("name", "")).strip()
            raw_folders = value.get("folders", [])
        else:
            raw_name = str(value).strip()
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
        categories.append(CategoryConfig(name=category_name, folders=folders))

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


def load_config(config_path: Path) -> AppConfig:
    with _CONFIG_IO_LOCK:
        with config_path.open("r", encoding="utf-8") as handle:
            raw_data = yaml.safe_load(handle) or {}

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
    )
    if not paths.base_storage_path or not paths.incoming_temp_path or not paths.database_path:
        raise ValueError("paths.base_storage_path, incoming_temp_path, and database_path are required.")

    behavior_raw = raw_data.get("behavior", {})
    behavior = BehaviorConfig(
        delete_telegram_message_after_save=bool(behavior_raw.get("delete_telegram_message_after_save", True)),
        duplicate_action=str(behavior_raw.get("duplicate_action", "skip")).strip() or "skip",
        allow_new_folder=bool(behavior_raw.get("allow_new_folder", True)),
        keep_original_filename=bool(behavior_raw.get("keep_original_filename", True)),
    )

    raw_categories = raw_data.get("categories", [])
    if not isinstance(raw_categories, list):
        raise ValueError("categories must be a list.")

    uses_nested_categories = raw_categories and all(isinstance(value, dict) for value in raw_categories) and (
        any(isinstance(value, dict) and "folders" in value for value in raw_categories)
        or ("products" not in raw_data and "folder_map" not in raw_data)
    )

    if uses_nested_categories:
        categories = _normalize_category_list(raw_categories)
    else:
        categories = _load_legacy_category_list(raw_data)

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

    return AppConfig(
        telegram_bot_token=telegram_bot_token,
        server=server,
        security=security,
        paths=paths,
        behavior=behavior,
        categories=categories,
        logging=logging_config,
        webui=webui,
    )


def save_config(config: AppConfig, config_path: Path, create_backup: bool = True) -> Path | None:
    with _CONFIG_IO_LOCK:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path: Path | None = None

        if create_backup and config_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = config_path.with_name(f"config.backup.{timestamp}.yaml")
            backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

        temp_path = config_path.with_name(f"{config_path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(config.to_dict(), handle, sort_keys=False, allow_unicode=False)
        temp_path.replace(config_path)

        return backup_path
