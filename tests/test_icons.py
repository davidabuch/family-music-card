"""Approved artwork, install metadata and real static-handler regression checks."""

import importlib.util
import io
import json
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / "standalone"
spec = importlib.util.spec_from_file_location("icon_server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class IconTests(unittest.TestCase):
    def request(self, path, host):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler.headers = Message()
        handler.headers["Host"] = host
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
            handler.do_GET()
        self.assertEqual(handler.send_response.call_args.args[0], 200)
        return handler.wfile.getvalue(), dict(
            call.args for call in handler.send_header.call_args_list
        )

    def test_install_metadata_and_matching_local_remote_responses(self):
        manifest = json.loads((ROOT / "web/manifest.webmanifest").read_text())
        self.assertEqual({i["sizes"] for i in manifest["icons"]}, {"192x192", "512x512"})
        index = (ROOT / "web/index.html").read_text()
        self.assertIn('rel="apple-touch-icon" sizes="180x180"', index)
        self.assertIn('href="/icons/approved-v1/icon-180.png"', index)
        self.assertNotIn("icon.svg", index)
        assets = {i["src"] for i in manifest["icons"]}
        assets.update(f"/icons/approved-v1/icon-{size}.png" for size in (16, 32, 180))
        assets.add("/favicon.ico")
        for path in assets:
            with self.subTest(path=path):
                self.assertTrue(path.startswith("/icons/approved-v1/") or path == "/favicon.ico")
                local, headers = self.request(path + "?probe=local", "app-local.example.test")
                remote, _ = self.request(path + "?probe=remote", "app.example.test")
                self.assertEqual(local, remote)
                expected = (
                    ROOT
                    / "web"
                    / (
                        "icons/approved-v1/favicon.ico"
                        if path == "/favicon.ico"
                        else path.lstrip("/")
                    )
                )
                self.assertEqual(local, expected.read_bytes())
                self.assertEqual(headers["Content-Length"], str(len(local)))
                if path.endswith(".png"):
                    self.assertEqual(headers["Content-Type"], "image/png")
                    self.assertEqual(local[:8], b"\x89PNG\r\n\x1a\n")
                else:
                    self.assertIn(
                        headers["Content-Type"], ("image/x-icon", "image/vnd.microsoft.icon")
                    )

    def test_worker_precaches_only_approved_icons(self):
        worker = (ROOT / "web" / "service-worker.js").read_text()
        self.assertNotIn("icon.svg", worker)
        for size in (16, 32, 180, 192, 512):
            self.assertIn(f'"/icons/approved-v1/icon-{size}.png"', worker)
        self.assertIn("approved-icons-v1", worker)
        self.assertIn("/api/", worker)
