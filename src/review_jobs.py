from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from threading import Lock
from typing import Any, Callable
from uuid import uuid4


class ReviewJobStore:
    """Thread-safe, process-local tracking for short review move jobs."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._reserved_item_ids: set[int] = set()

    def create_move_job(self, item_ids: list[int], destination: str) -> dict[str, Any]:
        normalized_ids = list(dict.fromkeys(int(item_id) for item_id in item_ids))
        with self._lock:
            conflicts = sorted(set(normalized_ids) & self._reserved_item_ids)
            if conflicts:
                raise ValueError("One or more selected files are already being moved.")

            job_id = uuid4().hex
            now = datetime.utcnow().isoformat()
            job = {
                "id": job_id,
                "kind": "move",
                "status": "queued",
                "item_ids": normalized_ids,
                "destination": destination,
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "result": None,
                "error": "",
            }
            self._jobs[job_id] = job
            self._reserved_item_ids.update(normalized_ids)
            return deepcopy(job)

    def run(self, job_id: str, worker: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = "running"
            job["started_at"] = datetime.utcnow().isoformat()

        try:
            result = worker()
        except Exception as exc:  # pragma: no cover - defensive job boundary
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "failed"
                job["error"] = str(exc)
                job["completed_at"] = datetime.utcnow().isoformat()
        else:
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "completed"
                job["result"] = result
                job["completed_at"] = datetime.utcnow().isoformat()
        finally:
            with self._lock:
                job = self._jobs.get(job_id)
                if job:
                    self._reserved_item_ids.difference_update(job["item_ids"])

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def reserved_item_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._reserved_item_ids)
