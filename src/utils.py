from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, List


INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")


def dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def clean_name(value: str) -> str:
    cleaned = INVALID_PATH_CHARS.sub("_", value.strip())
    cleaned = WHITESPACE.sub(" ", cleaned)
    cleaned = cleaned.strip(" .")
    return cleaned or "untitled"


def sanitize_filename(file_name: str) -> str:
    path = Path(file_name)
    stem = clean_name(path.stem)
    suffix = clean_name(path.suffix) if path.suffix else ""
    if suffix and not suffix.startswith("."):
        suffix = f".{suffix}"
    return f"{stem}{suffix}"


def compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def unique_path(target_path: Path) -> Path:
    if not target_path.exists():
        return target_path

    stem = target_path.stem
    suffix = target_path.suffix
    parent = target_path.parent

    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter:03d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def parse_multiline_list(value: str) -> list[str]:
    raw_parts = re.split(r"[\n,]+", value or "")
    cleaned = [clean_name(part) for part in raw_parts if part.strip()]
    return dedupe_preserve_order(cleaned)
