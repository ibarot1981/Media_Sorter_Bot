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
        self.review_thumbnail_path = ensure_directory(Path(config.paths.review_thumbnail_path))
        processed_path = config.paths.syncthing_processed_path or str(self.incoming_temp_path / "_processed")
        self.syncthing_processed_path = ensure_directory(Path(processed_path))

    def build_temp_download_path(self, original_file_name: str) -> Path:
        safe_name = sanitize_filename(original_file_name)
        return self.incoming_temp_path / f"{uuid4().hex}_{safe_name}"

    def build_destination_directory(self, category: str, folder_path: list[str] | tuple[str, ...]) -> Path:
        destination = self.config.get_category_root_path(category)
        if destination is None:
            raise ValueError(
                f"Category '{category}' does not have a configured root_path and no fallback base_storage_path is set."
            )
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

    def build_review_thumbnail_path(self, item_id: int, original_file_name: str) -> Path:
        safe_name = sanitize_filename(Path(original_file_name).stem) or "preview"
        return self.review_thumbnail_path / f"{item_id}_{safe_name}.jpg"

    def finalize_review_item(
        self,
        source_path: Path,
        original_file_name: str,
        category: str,
        folder_path: list[str] | tuple[str, ...],
        *,
        delete_source_after_save: bool,
    ) -> Path:
        destination_dir = self.build_destination_directory(category, folder_path)
        final_name = sanitize_filename(original_file_name)
        final_path = unique_path(destination_dir / final_name)
        if delete_source_after_save:
            shutil.move(str(source_path), str(final_path))
        else:
            shutil.copy2(source_path, final_path)
        return final_path

    def archive_review_item(self, source_path: Path, bucket_name: str) -> Path:
        bucket_dir = ensure_directory(self.syncthing_processed_path / clean_name(bucket_name))
        final_path = unique_path(bucket_dir / sanitize_filename(source_path.name))
        shutil.move(str(source_path), str(final_path))
        return final_path
