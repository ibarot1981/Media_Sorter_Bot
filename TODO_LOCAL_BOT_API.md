# Local Bot API TODO

Goal: use Telegram's local Bot API server to solve large file handling now on the same machine, while keeping a clear future path for an always-on ingest architecture later if needed.

## Current Direction

Two phases are now planned:

1. Phase 1: same-machine Local Bot API
   - purpose: solve large file downloads
   - deployment: `telegram-bot-api` and the Python app run on the same machine
   - status target: production-ready for the current laptop workflow

2. Phase 2: future always-on ingest architecture
   - purpose: keep receiving and storing media even when the main processor app is down
   - deployment: separate always-on service, likely in Docker/Linux
   - status target: deferred until actually needed

This split is intentional and makes sense.

Phase 1 is the smallest useful improvement because it fixes the large-file limitation without forcing a bigger architectural change right now.

Phase 2 is a separate product capability, not just a deployment tweak, because Bot API updates are not retained indefinitely by Telegram and the current app queue is still in-memory.

## Phase 1 Target Outcome

- a single control entrypoint starts the local Bot API server first when enabled
- the Python app connects to the local Bot API server instead of the cloud Bot API
- large file downloads work through the local server
- the setup stays optional and config-driven
- the current single-machine bot workflow continues to behave the same when the feature is disabled

## Phase 1 Implementation Plan

1. Add local Bot API settings to config files.
   - Update `config.example.yaml`
   - Update `src/config.py` schema support
   - Add a new `local_bot_api` config section with fields such as:
   - `enabled`
   - `auto_start`
   - `base_url`
   - `base_file_url`
   - `http_host`
   - `http_port`
   - `binary_path`
   - `working_dir`
   - `temp_dir`
   - `log_file`

2. Extend the config model in `src/config.py`.
   - Add a `LocalBotAPIConfig` dataclass
   - Parse defaults safely
   - Keep the feature disabled by default so current behavior remains unchanged

3. Update bot startup in `src/bot.py`.
   - When `local_bot_api.enabled` is true, configure `python-telegram-bot` with:
   - `base_url`
   - `base_file_url`
   - `local_mode(True)`
   - Keep the current cloud behavior when the feature is disabled

4. Verify same-machine file handling assumptions.
   - The local Bot API in `--local` mode may return absolute local file paths
   - On the same machine this is fine and aligns with the current `download_to_drive(custom_path=...)` flow
   - Do not optimize yet for split-machine deployments in this phase

5. Add a Windows control/startup wrapper.
   - Create a single control script, for example `scripts/botctl.ps1`
   - Responsibilities:
   - start `telegram-bot-api.exe` if `auto_start` is enabled
   - bind it to `127.0.0.1`
   - pass `--local`
   - wait until the HTTP endpoint is ready
   - then launch `python -m src.main --mode all`
   - stop the local Bot API process on exit if the wrapper started it

6. Add a simple Windows entrypoint.
   - Add a small `botctl.bat` wrapper
   - Use actions like `start`, `start-background`, `stop`, `restart`, `status`, and `logout`

7. Add one-time cloud logout support.
   - Telegram requires the bot token to log out from the cloud Bot API before using a local Bot API server
   - Surface this through the control script as `logout`
   - This should be a manual one-time migration step, not something run automatically on every startup

8. Add fail-fast validation.
   - If local mode is enabled but the local server is unavailable, stop with a clear error
   - Do not silently fall back to cloud mode, because large files would fail again and be confusing

9. Update documentation.
   - Document setup in `README.md`
   - Include:
   - how to get `telegram-bot-api.exe`
   - how to provide Telegram `api_id` and `api_hash`
   - the one-time cloud logout step
   - how to use the control commands

## Phase 1 Design Notes

- Keep the local Bot API server bound to `127.0.0.1` only
- Prefer storing Telegram `api_id` and `api_hash` in environment variables instead of `config.yaml`
- Scope the first implementation to Windows, since startup is being tied to the Windows control script
- Use a preinstalled `telegram-bot-api.exe` path first rather than trying to auto-download or build the binary
- Do not redesign queue persistence as part of Phase 1

## Phase 1 Open Decisions

1. Where will `telegram-bot-api.exe` live?
   - inside the repo
   - or in a fixed system path

2. How should credentials be provided?
   - environment variables recommended:
   - `TELEGRAM_API_ID`
   - `TELEGRAM_API_HASH`

3. Should the control script always start the local server, or only when `local_bot_api.enabled: true`?
   - recommended: only when enabled

## Phase 1 Suggested Execution Order

1. `src/config.py`
2. `config.example.yaml`
3. `src/bot.py`
4. `scripts/botctl.ps1`
5. `botctl.bat`
6. `README.md`

## Phase 2 Future Architecture

This phase is intentionally deferred.

Implement it only when the current same-machine workflow becomes limiting and you truly need:

- always-on intake
- media retention while the main processor app is offline
- separation between Telegram intake and human classification/sorting work

## Phase 2 Goal

Keep receiving and storing media even when the main processing/classification app is offline for days or weeks.

## Phase 2 High-Level Architecture

Split the system into two services:

1. Ingest service
   - always on
   - receives Telegram updates
   - downloads media immediately
   - stores media in durable storage
   - records metadata and status in a database

2. Processor app
   - can be offline
   - wakes up later
   - reads pending items from the database
   - performs duplicate checks, classification, and final save

## Phase 2 Storage Strategy

Do not depend on shared local directories between the ingest host and the processor host.

Instead use durable shared storage such as:

- MinIO or other S3-compatible object storage
- NAS via SMB/NFS if practical
- another network-accessible file store

Recommended model:

- raw incoming media goes to object storage
- metadata and processing status go to SQLite first or Postgres later
- final saved media can remain in the existing folder tree model

## Phase 2 Data Model Direction

Add a persistent incoming-items table with fields such as:

- `id`
- `telegram_file_id`
- `telegram_message_id`
- `telegram_chat_id`
- `telegram_user_id`
- `original_file_name`
- `mime_type`
- `file_size`
- `storage_key`
- `sha256_hash`
- `status`
- `received_at`
- `processed_at`
- `error_message`

Suggested statuses:

- `received`
- `stored`
- `queued`
- `processing`
- `saved`
- `failed`

## Phase 2 Processing Flow

1. User sends media in Telegram
2. Ingest service receives update
3. Ingest service downloads media through Local Bot API
4. Ingest service stores the raw file durably
5. Ingest service writes a DB record with pending status
6. Processor app starts later
7. Processor app loads pending items
8. Processor app resumes duplicate detection, classification, and final save

## Phase 2 Notes

- Only one active receiver should still own the bot token at a time
- This is a larger refactor than Phase 1 because it changes the system boundary, persistence model, and operational flow
- The Telegram-facing intake service should be the always-on component, not the human-driven processor

## Decision Summary

Current decision:

- implement Phase 1 now
- do not build Phase 2 yet
- preserve a clean path for Phase 2 later

That is the right tradeoff for the current stage of the project.

## References

- Telegram Bot API local server docs:
  https://core.telegram.org/bots/api#using-a-local-bot-api-server
- Official `telegram-bot-api` repository:
  https://github.com/tdlib/telegram-bot-api
- `python-telegram-bot` ApplicationBuilder docs:
  https://docs.python-telegram-bot.org/en/v21.3/telegram.ext.applicationbuilder.html
