from __future__ import annotations

import logging

from src.database import Database
from src.models import DuplicateResult


class DuplicateChecker:
    def __init__(self, database: Database, duplicate_action: str) -> None:
        self.database = database
        self.duplicate_action = duplicate_action
        self.logger = logging.getLogger(__name__)

    def check(self, sha256_hash: str) -> DuplicateResult:
        existing = self.database.find_by_hash(sha256_hash)
        if not existing:
            return DuplicateResult(is_duplicate=False)

        if self.duplicate_action == "skip":
            return DuplicateResult(is_duplicate=True, existing_path=existing["saved_path"])

        self.logger.warning(
            "Duplicate detected for hash %s, but duplicate_action=%s is not fully implemented yet. "
            "Continuing to classification flow.",
            sha256_hash,
            self.duplicate_action,
        )
        return DuplicateResult(is_duplicate=False, existing_path=existing["saved_path"])
