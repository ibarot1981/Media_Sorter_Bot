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
  botctl.bat
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
    botctl.ps1
    get_runtime_config.py
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

## Control Commands

Use the universal control wrapper from the project root:

```powershell
.\botctl.bat help
```

Main actions:

- `.\botctl.bat start`
- `.\botctl.bat start-background`
- `.\botctl.bat stop`
- `.\botctl.bat restart`
- `.\botctl.bat status`
- `.\botctl.bat logout`

Notes:

- `start` keeps the bot attached to the current terminal
- `start-background` launches it in a hidden background PowerShell process
- `stop` stops this repo's Python app, local Bot API server, and launcher
- `restart` stops and starts again
- `status` shows whether the repo's bot processes are currently running
- `logout` performs Telegram's one-time cloud Bot API logout step

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

### Windows Local Bot API Mode

Use this when you want large file support and both the Telegram Bot API server and this Python app will run on the same Windows machine.

Important credential note:

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are tied to the Telegram application you create at `https://my.telegram.org`, not to a specific Windows machine and not to your bot token
- you can reuse the same pair on another machine later by setting the same environment variables there
- keep them secret, because they identify your Telegram application
- Telegram's official docs say each phone number can only have one `api_id` connected to it, so you usually create these once and reuse them

Setup:

1. Download `telegram-bot-api.exe` from the official Telegram Bot API project and place it either:
   - in the repo root, or
   - somewhere on your `PATH`, or
   - at a custom path configured in `local_bot_api.binary_path`
2. Get your Telegram API credentials from `https://my.telegram.org`:

   - sign in with your Telegram account
   - open `API development tools`
   - fill the form with values like:
   - `App Title`: `Media Sorter Bot`
   - `Short Name`: `mediasorterbot`
   - `URL`: `http://localhost`
   - `Platform`: `Desktop`
   - `Description`: `Private local Telegram integration for my Media Sorter Bot on Windows`
   - click `Create application`
   - copy the `api_id` and `api_hash`

3. Set your Telegram API credentials in the Windows user environment:

```powershell
setx TELEGRAM_API_ID "your_api_id"
setx TELEGRAM_API_HASH "your_api_hash"
```

4. Close PowerShell, open a new PowerShell window, and verify the variables are available:

```powershell
echo $env:TELEGRAM_API_ID
echo $env:TELEGRAM_API_HASH
```

5. Enable local mode in `config.yaml`:

```yaml
local_bot_api:
  enabled: true
  auto_start: true
  base_url: "http://127.0.0.1:8081/bot"
  base_file_url: "http://127.0.0.1:8081/file/bot"
  http_host: "127.0.0.1"
  http_port: 8081
  binary_path: "telegram-bot-api.exe"
  working_dir: "data/telegram-bot-api"
  temp_dir: "data/telegram-bot-api/tmp"
  log_file: "logs/telegram-bot-api.log"
```

6. Run the one-time cloud logout before your first local start:

```powershell
.\botctl.bat logout
```

7. Start everything in the foreground:

```powershell
.\botctl.bat start
```

To start everything in the background:

```powershell
.\botctl.bat start-background
```

To stop everything:

```powershell
.\botctl.bat stop
```

To restart everything:

```powershell
.\botctl.bat restart
```

To check whether it is running:

```powershell
.\botctl.bat status
```

What `botctl.bat start` now does:

- starts `telegram-bot-api.exe` when `local_bot_api.enabled` and `local_bot_api.auto_start` are both true
- waits for the local Bot API server to become ready
- starts `python -m src.main --mode all`
- stops the local Bot API process when the Python app exits

What `botctl.bat start-background` now does:

- launches the same startup flow in a hidden background PowerShell process
- refuses to start if this repo's bot processes already appear to be running
- leaves the services running after the launch command returns
- pairs with `botctl.bat stop` and `botctl.bat restart`

Notes:

- this mode is for same-machine use only
- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` come from `https://my.telegram.org`
- a regular PowerShell window is enough for this setup; admin PowerShell is not required
- the current repo root already works with a locally built `telegram-bot-api.exe` plus its required DLLs
- if you started the app in an interactive terminal and kept that window open, `Ctrl+C` in that same window will also stop both services cleanly
- if you started the app with `botctl.bat start-background`, use `botctl.bat stop` to stop it because there is no attached interactive terminal
- after a successful `logOut`, Telegram does not allow moving back to the cloud Bot API for about 10 minutes
- if local mode is enabled and the local Bot API server is unavailable, startup fails fast instead of silently falling back

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
