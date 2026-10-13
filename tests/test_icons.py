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
        self.assertIn('href="/apple-touch-icon.png?v=approved-v2"', index)
        self.assertNotIn("icon.svg", index)
        self.assertNotIn("approved-v1", index)
        self.assertIn('rel="apple-touch-icon-precomposed"', index)
        self.assertIn("/apple-touch-icon-precomposed.png?v=approved-v2", index)
        assets = {i["src"] for i in manifest["icons"]}
        assets.update(f"/icons/approved-v2/icon-{size}.png" for size in (16, 32, 180))
        assets.update(
            ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png")
        )
        for path in assets:
            with self.subTest(path=path):
                self.assertTrue(
                    path.startswith("/icons/approved-v2/")
                    or path
                    in (
                        "/favicon.ico",
                        "/apple-touch-icon.png",
                        "/apple-touch-icon-precomposed.png",
                    )
                )
                local, headers = self.request(path + "?probe=local", "192.168.1.125:8099")
                remote, _ = self.request(path + "?probe=remote", "music.davidbuchmd.com")
                self.assertEqual(local, remote)
                if path.startswith("/apple-touch-icon"):
                    bare, _ = self.request(path, "192.168.1.125:8099")
                    self.assertEqual(bare, local)
                    self.assertEqual(headers["Cache-Control"], "no-cache")
                    versioned, _ = self.request(path + "?v=approved-v2", "music.davidbuchmd.com")
                    self.assertEqual(versioned, local)
                expected = (
                    ROOT
                    / "web"
                    / (
                        "icons/approved-v2/favicon.ico"
                        if path == "/favicon.ico"
                        else "icons/approved-v2/icon-180.png"
                        if path.startswith("/apple-touch-icon")
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
            self.assertIn(f'"/icons/approved-v2/icon-{size}.png"', worker)
        self.assertIn("approved-icons-v2", worker)
        self.assertIn("/api/", worker)
        self.assertIn("/apple-touch-icon.png?v=approved-v2", worker)
        self.assertIn("/apple-touch-icon-precomposed.png?v=approved-v2", worker)
        self.assertNotIn("approved-v1", worker)
