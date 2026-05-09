from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
import threading
from pathlib import Path

import uvicorn

from src.bot import MediaSorterBot
from src.config import load_config
from src.database import Database
from src.duplicates import DuplicateChecker
from src.review_queue import ReviewQueueService
from src.runtime_lock import RuntimeLock, RuntimeLockError
from src.storage import StorageService
from src.utils import ensure_directory
from src.webui import create_web_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Media Sorter Bot")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML file.")
    parser.add_argument(
        "--mode",
        choices=["bot", "webui", "watcher", "all"],
        default="bot",
        help="Run the Telegram bot, the local web UI, the Syncthing watcher, or all services together.",
    )
    return parser.parse_args()


def setup_logging(config) -> None:
    log_file = Path(config.logging.file)
    ensure_directory(log_file.parent)

    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def run_webui(
    config_path: Path,
    host: str,
    port: int,
    *,
    database: Database | None = None,
    storage: StorageService | None = None,
    duplicate_checker: DuplicateChecker | None = None,
) -> None:
    app = create_web_app(
        config_path,
        database=database,
        storage=storage,
        duplicate_checker=duplicate_checker,
    )
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    setup_logging(config)

    ensure_directory(Path(config.paths.incoming_temp_path))
    ensure_directory(Path(config.paths.database_path).parent)
    ensure_directory(Path(config.paths.review_thumbnail_path))
    lock = RuntimeLock(Path(config.paths.database_path).parent / "media_sorter_app.lock")

    try:
        lock.acquire()
    except RuntimeLockError as exc:
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(1) from exc

    try:
        if args.mode == "webui":
            database = Database(Path(config.paths.database_path))
            storage = StorageService(config)
            duplicate_checker = DuplicateChecker(database, config.behavior.duplicate_action)
            run_webui(
                config_path,
                config.webui.host,
                config.webui.port,
                database=database,
                storage=storage,
                duplicate_checker=duplicate_checker,
            )
            return

        database = Database(Path(config.paths.database_path))
        storage = StorageService(config)
        duplicate_checker = DuplicateChecker(database, config.behavior.duplicate_action)

        if args.mode == "watcher":
            review_queue = ReviewQueueService(config, config_path, database, storage, duplicate_checker)
            review_queue.run_forever()
            return

        bot = MediaSorterBot(config, config_path, database, storage, duplicate_checker)
        review_queue = ReviewQueueService(config, config_path, database, storage, duplicate_checker)

        if args.mode == "all":
            web_thread = threading.Thread(
                target=run_webui,
                args=(config_path, config.webui.host, config.webui.port),
                kwargs={
                    "database": database,
                    "storage": storage,
                    "duplicate_checker": duplicate_checker,
                },
                daemon=True,
            )
            watcher_thread = threading.Thread(
                target=review_queue.run_forever,
                daemon=True,
            )
            web_thread.start()
            watcher_thread.start()

        bot.run()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
