from __future__ import annotations

import unittest
from ipaddress import ip_network
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from src.config import DEFAULT_WEBUI_TRUSTED_NETWORKS, normalize_trusted_networks
from src.database import Database
from src.webui import _is_trusted_client, create_web_app


class TrustedNetworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.networks = tuple(ip_network(value) for value in DEFAULT_WEBUI_TRUSTED_NETWORKS)

    def test_loopback_and_private_clients_are_allowed(self) -> None:
        for address in ("127.0.0.1", "192.168.1.25", "10.20.30.40", "::1", "fd12::5"):
            with self.subTest(address=address):
                self.assertTrue(_is_trusted_client(address, self.networks))

    def test_public_and_invalid_clients_are_rejected(self) -> None:
        for address in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "not-an-ip", None):
            with self.subTest(address=address):
                self.assertFalse(_is_trusted_client(address, self.networks))

    def test_ipv4_mapped_ipv6_is_checked_as_ipv4(self) -> None:
        self.assertTrue(_is_trusted_client("::ffff:192.168.1.25", self.networks))
        self.assertFalse(_is_trusted_client("::ffff:8.8.8.8", self.networks))

    def test_network_values_are_canonicalized_and_deduplicated(self) -> None:
        self.assertEqual(
            normalize_trusted_networks(["192.168.1.42/24", "192.168.1.0/24"]),
            ["192.168.1.0/24"],
        )

    def test_empty_or_invalid_allowlists_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_trusted_networks([])
        with self.assertRaises(ValueError):
            normalize_trusted_networks(["not-a-network"])

    def test_web_app_allows_loopback_and_rejects_public_clients(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "categories.yaml").write_text(
                "categories:\n  - name: Test\n    root_path: storage\n",
                encoding="utf-8",
            )
            config_path = root / "config.yaml"
            config_path.write_text(
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
                        f'  incoming_temp_path: "{(root / "incoming").as_posix()}"',
                        f'  database_path: "{(root / "test.db").as_posix()}"',
                        "folder_config:",
                        '  categories_file: "categories.yaml"',
                        "webui:",
                        '  host: "0.0.0.0"',
                        "  port: 8080",
                        "  trusted_networks:",
                        '    - "127.0.0.0/8"',
                    ]
                ),
                encoding="utf-8",
            )

            database = Database(root / "test.db")
            try:
                app = create_web_app(config_path, database=database)
                with TestClient(app, client=("127.0.0.1", 50000)) as trusted_client:
                    self.assertEqual(trusted_client.get("/review").status_code, 200)
                with TestClient(app, client=("8.8.8.8", 50000)) as public_client:
                    self.assertEqual(public_client.get("/review").status_code, 403)
            finally:
                database.close()


if __name__ == "__main__":
    unittest.main()
