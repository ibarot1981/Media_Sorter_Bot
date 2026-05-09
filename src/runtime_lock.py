from __future__ import annotations

import atexit
from pathlib import Path


class RuntimeLockError(RuntimeError):
    pass


class RuntimeLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._handle = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+b")
        try:
            self._lock_handle(handle)
            handle.seek(0)
            handle.truncate()
            handle.write(str(Path.cwd()).encode("utf-8", errors="replace"))
            handle.flush()
        except Exception:
            handle.close()
            raise

        self._handle = handle
        atexit.register(self.release)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._unlock_handle(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    def _lock_handle(self, handle) -> None:
        try:
            import msvcrt  # type: ignore

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeLockError(
                    f"Another Media Sorter Bot instance is already running (lock: {self.lock_path})."
                ) from exc
            return
        except ImportError:
            pass

        import fcntl  # type: ignore

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeLockError(
                f"Another Media Sorter Bot instance is already running (lock: {self.lock_path})."
            ) from exc

    def _unlock_handle(self, handle) -> None:
        try:
            import msvcrt  # type: ignore

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        except ImportError:
            pass

        import fcntl  # type: ignore

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
