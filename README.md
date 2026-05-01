# Media Sorter Bot

Media Sorter Bot is a Telegram bot plus local Python app for sorting photos, videos, and documents from a phone into local folders on a Windows PC or Raspberry Pi/Debian box.

Architecture:

`Phone -> Telegram Bot -> Local Python App -> Local Category Folder Tree`

This repo implements:

- Phase 1: Telegram upload handling, classification with inline buttons, local save, optional Telegram message deletion after successful save
- Phase 2: YAML-driven config, SQLite record tracking, SHA-256 duplicate detection with default skip behavior
- Phase 3: Local web UI for editing `config.yaml`, nested category folder-tree builder, and automatic backup before save

Current limitation:

- Only one installation should actively poll the same Telegram bot token at a time.
- The code is structured for future multi-server routing, but Phase 1 still assumes a single active poller.

Future multi-server options:

- Separate bot token per server
- Central dispatcher
- Webhook routing
- Server selection command with central routing ownership

## Project Layout

```text
media-sorter-bot/
  README.md
  requirements.txt
  config.example.yaml
  config.yaml
  src/
    main.py
    bot.py
    webui.py
    config.py
    storage.py
    database.py
    duplicates.py
    models.py
    utils.py
  templates/
    config.html
  static/
    style.css
  scripts/
    install_debian_service.sh
    install_windows_task.ps1
  data/
    .gitkeep
  logs/
    .gitkeep
```

## What The App Does

1. An approved Telegram user sends photos, videos, or documents to the bot.
2. The local app downloads each file into a temp folder.
3. The app calculates a SHA-256 hash and checks SQLite for an exact duplicate.
4. Non-duplicates enter a per-user sequential queue.
5. The bot asks for:
   - Category
   - Folder and subfolder navigation under that category root
   - Recent save locations from the current user session for one-tap reuse
6. The file is copied into the final local folder using a safe filename.
7. If configured, the original Telegram media message is deleted only after the local save succeeds.

In the current single-server flow, approved users are auto-activated for the running server. `/start` is optional and mainly useful as a readiness check.

## Create The Telegram Bot

1. Open Telegram and chat with `@BotFather`.
2. Run `/newbot`.
3. Follow the prompts and copy the token.
4. Paste the token into `config.yaml` under `telegram_bot_token`.

## Get Your Telegram User ID

Options:

1. Message a helper bot like `@userinfobot` and note the numeric user ID.
2. Temporarily add logging or create a small debug command later if you want the bot to echo your ID.

Add the numeric ID to:

```yaml
security:
  allowed_telegram_user_ids:
    - 123456789
```

## Configure `config.yaml`

Start from `config.example.yaml` or edit the included `config.yaml`.

Important fields:

- `server.server_id`: unique ID for this installation
- `server.server_name`: user-facing name shown in Telegram
- `paths.base_storage_path`: root output folder
- `paths.incoming_temp_path`: temp download folder
- `paths.database_path`: SQLite file path
- `behavior.delete_telegram_message_after_save`: delete Telegram message only after save succeeds
- `behavior.duplicate_action`: default is `skip`
- `behavior.allow_new_folder`: show `New Folder` option during classification
- `categories`: category roots shown in Telegram, each with its own nested `folders` tree

Sync behavior:

- if a user creates a folder from Telegram, that new folder path is saved to disk and appended back into `config.yaml`
- if you create folders directly on disk, use the web UI import action to merge them back into `config.yaml`
- disk import is additive only and does not remove missing folders from YAML automatically

Example final path:

- Windows: `D:\Media\Kids\Birthdays\2026\image.jpg`
- Linux: `/home/pi/media/Products/PM Hoist/Control Panel/image.jpg`

## Install

### Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m src.main --mode bot
```

To run only the web editor:

```powershell
python -m src.main --mode webui
```

To run both:

```powershell
python -m src.main --mode all
```

Optional auto-start helper:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

### Raspberry Pi / Debian

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.main --mode bot
```

Run only the web editor:

```bash
python -m src.main --mode webui
```

Run both:

```bash
python -m src.main --mode all
```

Optional service helper:

```bash
chmod +x scripts/install_debian_service.sh
./scripts/install_debian_service.sh
```

## Run The Web UI Config Editor

By default the UI runs at:

`http://127.0.0.1:8080`

It lets you edit:

- server ID and server name
- approved Telegram user IDs
- storage and temp paths
- categories and their nested folder trees
- duplicate behavior
- delete-after-save behavior
- import selected category trees from disk into YAML

When you save:

- input is validated
- the current config is backed up as `config.backup.YYYYMMDD_HHMMSS.yaml`
- `config.yaml` is rewritten
- you can optionally create missing folders on disk

When you import selected categories from disk:

- folders found under `base_storage_path/<category>` are merged into the selected category trees
- existing YAML entries are preserved
- missing disk folders are not pruned automatically

Security warning:

- Do not expose the web UI to the public internet.
- Keep it bound to `127.0.0.1` unless you add your own network protection.

## Duplicate Detection

Duplicate detection uses SHA-256 of the downloaded temp file.

Default behavior:

- if the same hash already exists in SQLite with status `saved`, the new file is skipped
- the bot replies with the existing saved path

Current Phase 1-3 behavior fully supports:

- `duplicate_action: skip`

The config also accepts `ask` and `save_anyway` for future extension, but interactive duplicate branching is not yet implemented.

## Telegram Message Deletion

- Telegram messages are deleted only when the local file copy succeeds
- if the local save fails, the original Telegram message is kept
- duplicate skips also delete the Telegram message when `delete_telegram_message_after_save` is enabled

## Notes On Queueing

- queueing is maintained per Telegram user
- multiple files are processed sequentially
- progress messages use the `Processing X of Y` pattern
- the bot remembers a small set of recent destinations per user session so repeated saves can reuse one tap
- the queue is in-memory for now, with clearer hooks for future persistence work

## Category Tree Model

Each category is a root folder under `paths.base_storage_path`.

Example:

```yaml
categories:
  - name: "Kids"
    folders:
      - name: "Birthdays"
        folders:
          - name: "2026"
          - name: "2027"
      - name: "School"
  - name: "Products"
    folders:
      - name: "PM Hoist"
        folders:
          - name: "Machine Photos"
          - name: "Control Panel"
```

In Telegram, the user first picks a category, then browses folders under that category until they choose the current folder or create a new one.

If they create a new folder from Telegram, that folder path is also written back into `config.yaml` so the web UI stays in sync.

## Multi-Server Design Notes

Each installation has its own:

- `server_id`
- `server_name`

The current single-server session flow is:

1. Send media directly, or send `/start` if you want a confirmation message
2. The running installation auto-activates itself for that approved user session
3. Upload media and classify it

That keeps the interface aligned with a future multi-server design, even though only one polling instance should be active right now.
