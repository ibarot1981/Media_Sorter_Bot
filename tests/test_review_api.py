from __future__ import annotations

import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.database import Database
from src.models import PendingReviewItem
from src.webui import create_web_app


class ReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.inbox = self.root / "inbox"
        self.destination = self.root / "sorted"
        self.inbox.mkdir()
        self.destination.mkdir()
        (self.root / "categories.yaml").write_text(
            "categories:\n"
            "  - name: Documents\n"
            f'    root_path: "{self.destination.as_posix()}"\n'
            "    folders:\n"
            "      - name: 2026\n",
            encoding="utf-8",
        )
        self.config_path = self.root / "config.yaml"
        self.config_path.write_text(
            "\n".join(
                [
                    'telegram_bot_token: "test-token"',
                    "server:",
                    '  server_id: "test"',
                    '  server_name: "Test"',
                    "security:",
                    "  allowed_telegram_user_ids:",
                    "    - 1",
                    "paths:",
                    f'  incoming_temp_path: "{(self.root / "temp").as_posix()}"',
                    f'  database_path: "{(self.root / "test.db").as_posix()}"',
                    f'  syncthing_inbox_path: "{self.inbox.as_posix()}"',
                    f'  syncthing_processed_path: "{(self.root / "processed").as_posix()}"',
                    f'  review_thumbnail_path: "{(self.root / "thumbs").as_posix()}"',
                    "folder_config:",
                    '  categories_file: "categories.yaml"',
                    "webui:",
                    '  host: "127.0.0.1"',
                    "  port: 8080",
                    "  trusted_networks:",
                    '    - "127.0.0.0/8"',
                    "review_queue:",
                    "  enabled: true",
                    "  delete_inbox_file_after_save: true",
                ]
            ),
            encoding="utf-8",
        )
        self.database = Database(self.root / "test.db")
        self.app = create_web_app(self.config_path, database=self.database)
        self.client = TestClient(self.app, client=("127.0.0.1", 50000))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.database.close()
        self.temp_dir.cleanup()

    def add_item(
        self,
        file_name: str = "drawing.txt",
        *,
        mime_type: str = "text/plain",
        content: bytes = b"test media",
    ) -> int:
        source = self.inbox / file_name
        source.write_bytes(content)
        return self.database.insert_pending_item(
            PendingReviewItem(
                intake_source="syncthing",
                source_path=str(source),
                source_modified_at=source.stat().st_mtime,
                source_root=str(self.inbox),
                source_relative_path=file_name,
                source_label="Inbox",
                original_file_name=file_name,
                sha256_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
                file_size=source.stat().st_size,
                mime_type=mime_type,
                status="notified",
                batch_token="batch-one",
            )
        )

    def test_workspace_lists_pending_items(self) -> None:
        item_id = self.add_item()
        response = self.client.get("/api/v1/review/workspace")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["items"]], [item_id])
        self.assertEqual(payload["categories"][0]["name"], "Documents")
        self.assertTrue(payload["categories"][0]["has_children"])
        self.assertNotIn("children", payload["categories"][0])

    def test_destination_tree_loads_children_and_searches_on_demand(self) -> None:
        children = self.client.get(
            "/api/v1/review/destinations",
            params={"category": "Documents", "parent": ""},
        )
        self.assertEqual(children.status_code, 200)
        self.assertEqual(
            children.json()["items"],
            [{"name": "2026", "path": "2026", "has_children": False}],
        )

        search = self.client.get("/api/v1/review/destinations", params={"q": "2026"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["items"][0]["value"], "Documents / 2026")

    def test_unknown_destination_parent_returns_not_found(self) -> None:
        response = self.client.get(
            "/api/v1/review/destinations",
            params={"category": "Documents", "parent": "missing"},
        )
        self.assertEqual(response.status_code, 404)

    def test_move_requires_csrf_token(self) -> None:
        item_id = self.add_item()
        response = self.client.post(
            "/api/v1/review/move",
            json={"item_ids": [item_id], "destination": "Documents / 2026"},
        )
        self.assertEqual(response.status_code, 403)

    def test_full_image_endpoint_only_serves_pending_images(self) -> None:
        image_id = self.add_item("photo.jpg", mime_type="image/jpeg", content=b"image bytes")
        document_id = self.add_item("notes.txt")

        image_response = self.client.get(f"/review/media/{image_id}")
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response.headers["content-type"], "image/jpeg")
        self.assertEqual(image_response.content, b"image bytes")
        self.assertEqual(self.client.get(f"/review/media/{document_id}").status_code, 415)

    def test_background_move_removes_successful_item_from_workspace(self) -> None:
        item_id = self.add_item()
        response = self.client.post(
            "/api/v1/review/move",
            headers={"X-CSRF-Token": self.app.state.csrf_token},
            json={"item_ids": [item_id], "destination": "Documents / 2026"},
        )
        self.assertEqual(response.status_code, 202)
        job = self.client.get(f"/api/v1/review/jobs/{response.json()['id']}").json()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["saved_count"], 1)
        self.assertTrue((self.destination / "2026" / "drawing.txt").is_file())
        workspace = self.client.get("/api/v1/review/workspace").json()
        self.assertEqual(workspace["items"], [])

    def test_failed_move_remains_pending_with_error(self) -> None:
        item_id = self.add_item("locked.txt")
        with patch("src.storage.StorageService.finalize_review_item", side_effect=PermissionError("file is locked")):
            response = self.client.post(
                "/api/v1/review/move",
                headers={"X-CSRF-Token": self.app.state.csrf_token},
                json={"item_ids": [item_id], "destination": "Documents / 2026"},
            )

        self.assertEqual(response.status_code, 202)
        job = self.client.get(f"/api/v1/review/jobs/{response.json()['id']}").json()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["failed_count"], 1)
        item = self.database.get_pending_item(item_id)
        self.assertEqual(item["status"], "notified")
        self.assertIn("file is locked", item["error_message"])
        workspace = self.client.get("/api/v1/review/workspace").json()
        self.assertEqual([entry["id"] for entry in workspace["items"]], [item_id])
        self.assertIn("file is locked", workspace["items"][0]["error_message"])


if __name__ == "__main__":
    unittest.main()
