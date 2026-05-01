I want to build an end-to-end app called "Media Sorter Bot".

Implementation note:
The current app has evolved from the original product-specific plan below. Categories are now generic root folders, and each category owns its own nested folder tree instead of using a global `products` list plus `folder_map`.

Goal:
Create a Telegram bot + local server app that lets users send/share product photos and videos from phone to Telegram, classify each file from the phone using Telegram buttons, and copy the files into configured folders on a local PC/server.

Architecture:
Phone → Telegram Bot → Local Python App → Local Product/Site Folder

Important design requirement:
There will be ONE Telegram bot, but multiple possible local servers/installations can run this app. I do not want to hardcode "home" or "work". Each local server should have a configurable server_id / server_name in YAML. More than one server may exist now or later.

Target platforms:
1. Raspberry Pi running Debian/Linux
2. Windows desktop

Tech stack:
- Python
- python-telegram-bot
- SQLite
- YAML config
- pathlib for cross-platform paths
- FastAPI or Flask for local Web UI
- Clean modular code

Core requirements:

1. Telegram bot
- Receive photos, videos, and document uploads.
- Treat each uploaded media file as a separate item.
- If user sends 10 photos/videos, process them one by one in a queue.
- Ask classification questions using inline Telegram buttons.
- Allow multiple approved Telegram users.
- Approved user IDs should come from config.yaml.
- Keep original filenames wherever possible.
- If Telegram does not provide a usable original filename, create a safe fallback filename.
- Do not overwrite existing files. If filename exists, append counter like filename_001.jpg.

2. Multi-server design
Because one Telegram bot may be used by multiple local servers:
- Each app installation should have a unique server_id and server_name in config.yaml.
- The bot should only process messages intended for that server.
- Implement a simple server selection flow.
Example:
  User sends /start
  Bot shows available server name from this running instance.
  User can select/activate this server for current session.
- Since each running instance receives Telegram updates from the same bot token, design carefully to avoid multiple servers processing the same file.
- For Phase 1, assume only one server instance is running at a time.
- But structure the code so future multi-server routing can be added.
- Add TODO comments explaining how to extend this using server_id routing, webhook, or a central dispatcher later.

3. Classification flow
For each file, ask:
- Category:
  - Product Pics
  - Site Pics
  - Other configurable categories
- Product:
  - Product list should come from YAML config.
- Subfolder:
  - Configurable per category/product.
  - Allow "New Folder" option if enabled.
- Optional metadata can be added later:
  - year
  - customer/site name
  - notes

4. Folder storage
- Copy files to the configured final folder.
- Do not move from source temp until save is confirmed.
- Store in local folders based on YAML config.
Example Windows:
D:\Product Media\Product Pics\PM Hoist\2026\original_filename.jpg

Example Linux:
/home/pi/product-media/Site Pics/PM Hoist/Surat XYZ Site/original_filename.jpg

5. Delete Telegram message after save
- After successful copy to local folder, delete the original media message from Telegram chat.
- This should be configurable:
  delete_telegram_message_after_save: true/false
- Never delete Telegram message unless local save succeeded.

6. Duplicate detection
- Download incoming media to temp folder.
- Calculate SHA-256 hash.
- Store file records in SQLite.
- Default duplicate behavior:
  If exact hash already exists, skip automatically.
- Bot should reply:
  "Duplicate skipped. Already saved at: <existing_path>"
- Config should allow changing this later:
  duplicate_action: skip
  Future values: ask, save_anyway

7. SQLite database
Store:
- id
- server_id
- sha256_hash
- original_file_name
- telegram_file_id
- saved_path
- category
- product
- subfolder
- received_from_user_id
- received_from_username
- telegram_chat_id
- telegram_message_id
- date_received
- status: saved/skipped/error

8. YAML config
Create config.example.yaml with:
- telegram_bot_token
- server_id
- server_name
- allowed_telegram_user_ids
- base_storage_path
- incoming_temp_path
- database_path
- delete_telegram_message_after_save
- duplicate_action
- allow_new_folder
- categories
- products
- folder_map
- logging settings

Example config structure:

telegram_bot_token: "PUT_TOKEN_HERE"

server:
  server_id: "work_server_01"
  server_name: "Work Office PC"

security:
  allowed_telegram_user_ids:
    - 123456789
    - 987654321

paths:
  base_storage_path: "D:/Product Media"
  incoming_temp_path: "D:/Product Media/_incoming_temp"
  database_path: "data/media_sorter.db"

behavior:
  delete_telegram_message_after_save: true
  duplicate_action: "skip"
  allow_new_folder: true
  keep_original_filename: true

categories:
  - name: "Product Pics"
  - name: "Site Pics"

products:
  - name: "PM Hoist"
  - name: "Monkey Hoist"
  - name: "Concrete Mixer"
  - name: "Tower Hoist"

folder_map:
  "Product Pics":
    "PM Hoist":
      - "2026"
      - "Control Panel"
      - "Machine Photos"
    "Monkey Hoist":
      - "2026"
      - "Machine Photos"
  "Site Pics":
    "PM Hoist":
      - "2026"
      - "Customer Sites"
    "Concrete Mixer":
      - "2026"
      - "Customer Sites"

logging:
  level: "INFO"
  file: "logs/media_sorter.log"

9. Local Web UI
Add a simple local Web UI to edit config values without manually editing YAML.

Web UI requirements:
- Run locally, for example:
  http://localhost:8080
- Show current config.
- Allow editing:
  - server_id
  - server_name
  - allowed Telegram user IDs
  - base storage path
  - incoming temp path
  - categories
  - products
  - folder_map
  - delete message setting
  - duplicate action
- On submit, validate inputs and write back to config.yaml.
- Make a backup before overwriting config:
  config.backup.YYYYMMDD_HHMMSS.yaml
- Do not expose Web UI publicly by default.
- Bind to 127.0.0.1 unless configured otherwise.
- Add warning in README that Web UI should not be exposed to internet.

10. Folder config builder in Web UI
The Web UI should make it easy to build the YAML:
- Add/remove categories
- Add/remove products
- For each category + product combination, add/remove subfolders
- Save to config.yaml
- Optionally create missing folders on disk after saving config

11. Batch handling
- Maintain per-user queue.
- If multiple files are sent, process them sequentially.
- Show progress like:
  "Processing 3 of 10"
- Make the queue durable enough to not crash easily, but full persistence can be added later.

12. Reliability
- Use temp download first.
- Calculate hash before final save.
- Copy to final folder only after classification.
- Use safe filename sanitization.
- Avoid overwrites.
- Log all important actions.
- Handle errors gracefully.
- If save fails, do not delete Telegram message.

13. Project structure
Create:

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

14. README
Include:
- What the app does
- How to create Telegram bot using BotFather
- How to get Telegram user ID
- How to configure config.yaml
- How to run Web UI config editor
- How to run on Windows
- How to run on Raspberry Pi/Debian
- How duplicate detection works
- How Telegram message deletion works
- Current limitation: Phase 1 assumes only one server instance actively polling the same bot token at a time
- Future multi-server options:
  - Separate bot token per server
  - Central dispatcher
  - Webhook routing
  - Server selection command

Build in phases.

Phase 1:
- Basic Telegram bot
- Receive photo/video/document
- Ask category/product/subfolder
- Save locally with original filename
- Delete Telegram message after successful save if configured

Phase 2:
- YAML-driven products/folders
- SQLite database
- SHA-256 duplicate detection with default skip

Phase 3:
- Local Web UI for editing config.yaml
- Folder map builder
- Config backup before save

Phase 4:
- Batch queue improvements
- Debian systemd install script
- Windows auto-start script

For now, implement Phase 1, Phase 2, and a simple Phase 3 Web UI.

Before coding:
1. Inspect the;ervtlpkrlhtyujtkgjhnythhhhhhhhrxfhfygdcsjk. existing repo.
2. Propose the implementation plan.
3. Then create the files and code.
4. Keep code clean, modular, and easy to extend.
