from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from src.config import AppConfig
from src.utils import clean_name, ensure_directory, sanitize_filename, unique_path


class StorageService:
    def __init__(self, config: AppConfig) -> None:
        self.refresh_config(config)

    def refresh_config(self, config: AppConfig) -> None:
        self.config = config
        self.base_storage_path = Path(config.paths.base_storage_path)
        self.incoming_temp_path = ensure_directory(Path(config.paths.incoming_temp_path))

    def build_temp_download_path(self, original_file_name: str) -> Path:
        safe_name = sanitize_filename(original_file_name)
        return self.incoming_temp_path / f"{uuid4().hex}_{safe_name}"

    def build_destination_directory(self, category: str, folder_path: list[str] | tuple[str, ...]) -> Path:
        destination = self.base_storage_path / clean_name(category)
        for folder_name in folder_path:
            destination = destination / clean_name(folder_name)
        return ensure_directory(destination)

    def save_media(
        self,
        temp_path: Path,
        original_file_name: str,
        category: str,
        folder_path: list[str] | tuple[str, ...],
    ) -> Path:
        destination_dir = self.build_destination_directory(category, folder_path)
        final_name = sanitize_filename(original_file_name)
        final_path = unique_path(destination_dir / final_name)
        shutil.copy2(temp_path, final_path)
        return final_path

    def cleanup_temp_file(self, temp_path: Path) -> None:
        if temp_path.exists():
            temp_path.unlink()
