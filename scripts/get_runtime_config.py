from __future__ import annotations

import json
import sys
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/get_runtime_config.py <config-path>")

    config_path = Path(sys.argv[1])
    if not config_path.is_absolute():
        config_path = config_path.resolve()

    config = load_config(config_path)
    payload = {
        "config_path": str(config_path),
        "telegram_bot_token": config.telegram_bot_token,
        "local_bot_api": {
            "enabled": config.local_bot_api.enabled,
            "auto_start": config.local_bot_api.auto_start,
            "base_url": config.local_bot_api.base_url,
            "base_file_url": config.local_bot_api.base_file_url,
            "http_host": config.local_bot_api.http_host,
            "http_port": config.local_bot_api.http_port,
            "binary_path": config.local_bot_api.binary_path,
            "working_dir": config.local_bot_api.working_dir,
            "temp_dir": config.local_bot_api.temp_dir,
            "log_file": config.local_bot_api.log_file,
        },
    }
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
