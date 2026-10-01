# Syncthing + Mobile Review Upgrade Plan

## Implementation Status

Implemented in the current codebase:

- shared `pending_items` queue table and `review_batches` tables inside SQLite
- shared duplicate detection path for Telegram saves and Syncthing intake
- new Syncthing review config fields in `config.yaml`, config loader, and config editor
- new `watcher` runtime mode and upgraded `all` mode
- Syncthing inbox polling with stable-file detection
- automatic queue insertion for new Syncthing files
- duplicate Syncthing files get auto-archived into the processed area instead of entering review
- image thumbnail generation with cached JPEG previews
- video poster-frame previews when ffmpeg support is available
- PDF first-page previews when PyMuPDF support is available
- private review routes:
  - `/review`
  - `/review/batch/{batch_token}`
  - `/review/thumbnail/{item_id}`
- phone-friendly batch review UI with:
  - thumbnail cards
  - multi-select
  - destination datalist
  - save selected
  - skip selected
  - recent and favorite destination shortcuts
  - item-first review with completed items kept in a collapsible section
- batched Telegram notifications for pending Syncthing review items
- manual reopening of pending batches from Telegram without reminder spam
- final save flow from review UI into the same destination tree and duplicate DB
- trusted-network CIDR enforcement for all Web UI routes

Still intentionally pending:

- Telegram uploads joining the same review queue
- stronger signed-link or user-bound auth for review links
- richer document previews/icons
- per-item destination overrides inside the same batch
- source/media-type filters in the review UI
- duplicate grouping within the pending queue itself
- smarter batching/session suppression beyond the first global batch-window cooldown
- browse existing saved folders from phone, preview folder contents in a grid, and re-deliver selected original files back to Telegram

Current first-cut behavior notes:

- review notifications require `review_queue.review_base_url` to be useful from a phone on your private network; otherwise the fallback URL uses the configured local web host/port
- image thumbnails require `Pillow`
- video previews require `Pillow` and `imageio-ffmpeg`
- PDF previews require `Pillow` and `PyMuPDF`
- unsupported document formats use file-type placeholder tiles

Goal: extend the current app so it supports both Telegram intake and Syncthing folder intake, while keeping one shared database, one duplicate-check path, one destination/folder model, and one phone-friendly review workflow.

## Summary

Planned result:

- Telegram uploads continue to work
- Syncthing-delivered files can enter the same system automatically
- both intake methods share the same duplicate logic and database
- new Syncthing files trigger Telegram notifications in batches instead of one message per file
- notification opens a mobile-friendly private web UI for sorting files from the phone
- final save logic stays centralized in this app

Recommended approach:

- extend this project instead of creating a second project
- add a new intake pipeline and review queue inside the existing app
- use Telegram as the notification/control layer
- use the existing FastAPI app as the private mobile review UI

## Product Direction

Two intake paths should coexist:

1. Telegram intake
   - user sends file to bot
   - app downloads it
   - app processes it through existing save flow

2. Syncthing intake
   - user sends file to phone-side Syncthing folder
   - Syncthing syncs it to a local inbox folder on PC/server
   - watcher detects it
   - app creates pending review item
   - Telegram notifies the user in batches
   - user opens mobile review UI and assigns destination

This gives one system with two front doors.

## Core Design Principles

- single source of truth for saved files and duplicates
- separate intake from review from final save
- keep all destination/category logic centralized
- keep Telegram chat noise low with batch notifications
- optimize the review flow for phone usage, not desktop usage
- do not require public hosting; assume private web UI over local network / ZeroTier

## High-Level Architecture

`Phone -> Telegram Bot -> App Intake -> Shared Queue -> Final Save`

`Phone -> Syncthing -> Inbox Folder -> Watcher -> Shared Queue -> Final Save`

`Shared Queue -> Telegram batch notification -> Mobile review UI -> Final Save`

Main components:

1. Intake layer
   - Telegram upload intake
   - Syncthing inbox intake

2. Queue / persistence layer
   - shared pending-items table
   - shared duplicate detection
   - shared status tracking

3. Review layer
   - Telegram batch notifications
   - private mobile web UI
   - batch selection and destination assignment

4. Finalization layer
   - move/copy into final destination
   - optional cleanup from inbox
   - update DB

## Recommended New Runtime Model

Current app modes are:

- `bot`
- `webui`
- `all`

Recommended future model:

- `bot`
- `webui`
- `watcher`
- `all`

Where:

- `bot` runs Telegram bot only
- `webui` runs config editor and mobile review UI
- `watcher` watches Syncthing inbox and builds pending review items
- `all` runs bot + watcher + FastAPI app

## Shared Data Model

The current saved-file DB should stay in place.

Add a new persistent queue table, for example `pending_items`.

Suggested fields:

- `id`
- `intake_source`
  - `telegram`
  - `syncthing`
- `source_user_id`
- `source_chat_id`
- `source_message_id`
- `source_label`
  - examples: `Camera`, `WhatsApp`, `Downloads`, `Syncthing Inbox A`
- `source_inbox_path`
- `source_relative_path`
- `original_file_name`
- `stored_temp_path`
- `mime_type`
- `file_size`
- `sha256_hash`
- `thumbnail_path`
- `preview_type`
- `preview_status`
- `preview_error`
- `duration_seconds`
- `width`
- `height`
- `page_count`
- `status`
- `selected_category`
- `selected_folder_path`
- `batch_token`
- `created_at`
- `notified_at`
- `saved_at`
- `error_message`

Suggested statuses:

- `new`
- `pending_review`
- `review_in_progress`
- `saved`
- `duplicate_skipped`
- `failed`
- `ignored`

## Duplicate Detection Model

Use one shared duplicate model across both intake methods.

Recommended rule:

- every intake computes SHA-256
- before creating a pending review item, check the existing saved-records table
- if duplicate already exists with `saved` status:
  - mark the new item `duplicate_skipped`
  - optionally include it in a duplicate summary
- if not duplicate:
  - create or update pending review item

Optional later enhancement:

- detect duplicate within the pending queue too, not just saved items

## Syncthing Inbox Design

Recommended config additions:

- `paths.syncthing_inbox_path`
- `paths.syncthing_processed_path`
- `paths.review_thumbnail_path`

Optional richer version:

- `syncthing_sources`
  - list of inbox roots with labels
  - example:
    - `Camera Uploads`
    - `WhatsApp`
    - `Downloads`

Watcher responsibilities:

- detect newly created files in inbox
- wait until file is fully written and stable
- compute metadata and hash
- generate thumbnail/preview
- create pending review item
- enqueue it for notification batching

Important stability rule:

- do not process file immediately on first sight
- wait until:
  - file size stops changing for a short interval
  - and file can be opened cleanly

## Notification Strategy

Do not send one Telegram notification per file.

Recommended batching:

- gather newly created Syncthing items
- send one notification every 5 minutes if there are pending unnotified items
- include:
  - number of new files
  - optional source breakdown
  - one button/link to open mobile review UI

Example notification:

- `18 new files are ready to sort`
- `Sources: Camera (10), WhatsApp (5), Downloads (3)`
- button: `Open Review Queue`

Recommended batching rules:

- send at most one queue notification within a 5-minute window
- if user is already actively reviewing, suppress duplicate notifications
- if backlog grows, send summary updates rather than per-file updates

## Mobile Review UI

This should be added to the existing FastAPI app, but kept separate from the config editor.

Recommended route family:

- `/review`
- `/review/batch/{batch_token}`
- `/review/item/{id}`
- `/review/api/...`

UI goals:

- fully usable on phone
- fast thumbnail scanning
- batch selection
- apply one destination to many files
- quick access to recent destinations

Recommended main screen:

1. Queue summary header
   - pending count
   - source filters
   - batch age / newest time

2. Thumbnail grid
   - 10 to 20 items per batch by default
   - checkbox/select overlay
   - file type marker
   - source label
   - filename

3. Bulk action panel
   - choose category
   - choose folder path
   - recent destinations
   - apply to selected
   - skip selected

4. Preview/details drawer
   - larger preview
   - original filename
   - source label
   - file size
   - relative inbox path

Recommended first-cut features:

- select multiple files
- select all in current batch
- open image/video preview
- choose destination from category tree
- use recent destination
- save selected
- skip selected

Preview expectations for the review UI:

- images should show thumbnail tiles
- videos should show either a real poster frame or a clear video placeholder
- PDFs should show either a first-page preview later or a PDF placeholder initially
- documents should always have a recognizable file-type tile even without rich preview

Recommended later features:

- filter by source
- filter by media type
- batch by date/time cluster
- split queue into sub-batches
- keyboard shortcuts for desktop fallback
- browse an existing destination folder and its subfolders in a thumbnail grid, similar to a lightweight Google Photos view for that directory
- open a larger preview for a single item from that folder browser
- select one or many saved items and send the original files back to Telegram for download

## Future Upgrade: Library Browser + Telegram Redelivery

This requirement makes sense and fits the project well.

It adds a second phone-friendly workflow on top of the existing pending-review flow:

1. Review new inbox items and save them
2. Later browse already-saved folders from the phone and retrieve originals back through Telegram when needed

Recommended scope:

- browse the configured destination folder tree from the phone
- show folders and media items together in a grid for the current directory
- show smaller thumbnails for files in the current directory only
- show subfolders as folder tiles in the same grid
- open subfolders in-place using the same grid layout
- tap a file to open a larger preview/details view
- allow selecting one or many files
- provide an action to send selected original files to the authorized Telegram chat for download

Suggested routes:

- `/library`
- `/library/browse/{category}`
- `/library/browse/{category}/{path...}`
- `/library/item/{category}/{path...}`
- `/library/send-to-telegram`

Recommended UX:

1. Folder browser screen
   - breadcrumb path
   - current folder name
   - grid of folder tiles and file thumbnails
   - file type badges for non-image items
   - selection mode toggle

2. Item preview screen or drawer
   - larger image/video/PDF preview where possible
   - filename
   - size
   - modified time
   - folder path
   - `Send to Telegram` action

3. Bulk actions
   - select one or many files
   - `Send Selected to Telegram`
   - optional later `Download zip` or `Generate share bundle`

Recommended implementation phases:

### Phase A: Read-only folder browser

- add server-side folder listing for configured category roots
- show subfolders and files in one grid
- generate or reuse small thumbnails for existing saved media
- add breadcrumb navigation
- keep this read-only at first

### Phase B: Item preview

- add larger preview modal/page for a selected file
- support images first
- use placeholders or metadata cards for unsupported formats
- later extend to videos and PDFs using the same preview pipeline

### Phase C: Telegram redelivery

- add selection support in the browser
- add server action to queue selected files for Telegram delivery
- send originals back only to authorized Telegram users
- handle Telegram file-size and media-type limitations gracefully
- fall back to `sendDocument` when needed

### Phase D: Hardening

- access control for library browsing routes
- rate limits / batch limits for Telegram re-delivery
- audit logging for who requested which file
- clear user feedback for failed deliveries

Important design notes:

- this should browse only inside configured category roots; never allow arbitrary filesystem traversal
- subfolder navigation must validate and normalize paths at every step
- thumbnails for already-saved files should be cached separately from the pending-review thumbnail cache when useful
- Telegram redelivery should reuse the existing authorized-user model
- sending many large originals may need chunking or queueing to avoid blocking the bot

Good later enhancements:

- filter by file type inside the current folder
- sort by newest/oldest/name
- show recent folders
- show favorite folders
- optional ZIP bundling for multi-file download
- optional "open in review-style carousel" for swiping through media

## Destination Picker Design

The destination picker must be much simpler than the current config editor tree.

Recommended model:

- category first
- then folder tree browser
- then recent destinations
- then favorites

Helpful enhancements:

- searchable destination picker
- breadcrumb path preview
- favorite folders
- recently used folders pinned at top

## Telegram Bot Responsibilities After Upgrade

Telegram bot should keep doing:

- Telegram upload intake
- user authorization
- queue notifications
- review link sending

Telegram bot should not become the primary batch-sorting UI.

Best role for the bot:

- notify
- open review
- maybe provide quick summary actions

## FastAPI Responsibilities After Upgrade

FastAPI app will have two UI surfaces:

1. Config editor
   - admin/configuration

2. Mobile review UI
   - operational sorting workflow

Recommended separation:

- `/` or `/config` for config editor
- `/review` for queue review UI

This keeps concerns clear.

## File Lifecycle

For Syncthing intake:

1. file appears in inbox
2. watcher validates file is stable
3. watcher computes hash
4. watcher checks duplicates
5. watcher generates preview
6. watcher creates pending item
7. item waits for batch notification
8. user opens review UI
9. user selects destination
10. final save/move occurs
11. DB is updated
12. inbox file is either:
   - moved to final destination
   - or archived/removed from inbox

For Telegram intake:

1. bot receives file
2. app downloads file to temp
3. same shared duplicate check path runs
4. item uses either:
   - existing immediate bot save flow in first cut
   - or later can be normalized into the same pending review queue

## Integration Strategy for Telegram Intake

Two possible paths:

1. Keep current Telegram intake flow mostly as-is
   - lowest-risk first cut
   - Syncthing gets the new queue/review UI

2. Later unify Telegram intake into the same pending review queue
   - cleaner long-term architecture
   - more consistent UX

Recommended:

- Phase 1: keep Telegram upload flow working as-is
- Phase 2: optionally unify Telegram upload into shared queue

## Security / Access Model

Because there is no public HTTPS endpoint planned, assume private access only.

Recommended assumptions:

- review UI is reachable only over local network / ZeroTier
- do not expose review UI publicly
- require basic app-level auth or signed review links

Recommended minimum:

- Telegram notification link contains short-lived signed token
- token opens review queue for authorized user only

Optional stronger model:

- app login + Telegram-linked identity

## Thumbnail / Preview Strategy

Images:

- generate small JPEG previews

Videos:

- generate one poster frame thumbnail

PDFs:

- generic PDF icon first
- optional first-page preview later

Documents:

- generic icon first
- optional richer preview later

Store:

- thumbnail path in DB
- cache thumbnails on disk

## Preview and Metadata Roadmap

This system should be designed from the start to support richer previews by file type, even if the first implementation is intentionally simple.

Recommended preview model:

- images
  - thumbnail preview
- videos
  - poster frame thumbnail
  - optional duration and dimensions
- PDFs
  - first-page preview
  - optional page count
- other documents
  - generic file-type icon first

Suggested preview metadata fields:

- `preview_type`
  - `image_thumbnail`
  - `video_thumbnail`
  - `pdf_first_page`
  - `file_icon`
- `preview_status`
  - `pending`
  - `ready`
  - `failed`
  - `unsupported`
- `preview_error`
- `duration_seconds`
- `width`
- `height`
- `page_count`

Recommended implementation ladder:

### Preview V1

- images: real thumbnails
- videos: generic video icon, or poster frame only if easy tooling already exists
- PDFs: generic PDF icon
- other docs: generic file-type icon

### Preview V2

- videos:
  - poster frame thumbnail
  - duration
  - width/height
- PDFs:
  - first-page preview thumbnail
  - page count

### Preview V3

- videos:
  - better frame selection
  - optional multi-frame contact strip
- PDFs:
  - richer preview
  - zoom-friendly page rendering
- other docs:
  - optional limited text/document previews for supported formats

Important rule:

- preview generation must never block queue creation permanently
- if preview generation fails:
  - create the pending item anyway
  - use a generic icon fallback
  - store failure status in preview metadata

## Suggested Config Additions

Possible new config sections:

```yaml
paths:
  syncthing_inbox_path: "D:/Media Inbox"
  syncthing_processed_path: "D:/Media Inbox/_processed"
  review_thumbnail_path: "data/review_thumbnails"

review_queue:
  notification_batch_minutes: 5
  batch_size_default: 20
  delete_inbox_file_after_save: true
  generate_video_thumbnails: true
```

Optional richer source config:

```yaml
syncthing_sources:
  - name: "Camera"
    path: "D:/Media Inbox/Camera"
  - name: "WhatsApp"
    path: "D:/Media Inbox/WhatsApp"
  - name: "Downloads"
    path: "D:/Media Inbox/Downloads"
```

## Recommended Implementation Phases

### Phase 1: Queue Foundation

- add `pending_items` DB table
- add shared queue status model
- add Syncthing inbox config
- add watcher service
- add duplicate check integration

### Phase 2: Review Surface

- add thumbnail generation
- add `/review` routes
- add phone-friendly thumbnail grid UI
- add batch selection
- add destination picker with recent folders

### Phase 3: Telegram Notifications

- add 5-minute batching scheduler
- add queue summary notifications
- add review link generation

### Phase 4: Save Workflow Integration

- move selected items to final destination
- update DB consistently
- clean inbox after save
- add skip/ignore actions

### Phase 5: UX Refinement

- source filters
- recent/favorite destinations
- queue summaries
- duplicate group handling
- optional Telegram intake unification
- richer video/PDF/document previews

### Phase 6: Saved Library Browser

- browse saved category folders from the phone
- show subfolders and file thumbnails in the same grid
- open larger previews for individual files
- select one or many files from a folder
- send selected originals back to Telegram for download

## Open Decisions

1. Should Syncthing intake use one inbox path or multiple labeled inbox roots?
   - recommended: multiple labeled inbox roots if practical

2. Should saved files be moved from inbox or copied first?
   - recommended: move after successful final save

3. Should Telegram-uploaded files join the same review queue immediately?
   - recommended: not in first cut

4. Should the review UI allow per-item destination override inside a batch?
   - recommended: yes, but after bulk save flow exists

5. Should queue notification open directly to next pending batch or to a dashboard?
   - recommended: dashboard first, then batch entry from there

## Recommended First Cut

Build in this order:

1. DB queue table
2. watcher service
3. config additions
4. thumbnail generation
5. simple mobile review UI
6. Telegram batched notifications
7. final save/move from review UI

## Why This Upgrade Makes Sense

This design solves the core workflow problem:

- files arrive automatically through Syncthing
- phone storage can be cleared after sync
- sorting happens from the phone instead of on the PC
- Telegram remains useful, but only for notification/control
- one app owns all persistence and file movement logic

This is a strong extension of the current project and fits its purpose well.
