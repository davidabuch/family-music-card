from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "standalone" / "server.py"

spec = importlib.util.spec_from_file_location("family_music_standalone", SERVER_PATH)
assert spec and spec.loader
standalone = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standalone)


def test_normalize_item_keeps_core_music_fields():
    item = {
        "uri": "apple_music://track/123",
        "name": "Song",
        "media_type": "track",
        "favorite": True,
        "duration": 212,
        "artists": [{"name": "Artist", "uri": "apple_music://artist/7"}],
        "album": {"name": "Album", "uri": "apple_music://album/9"},
        "metadata": {"images": [{"path": "http://ma/image"}]},
    }

    result = standalone.normalize_item(item)

    assert result["uri"] == "apple_music://track/123"
    assert result["name"] == "Song"
    assert result["media_type"] == "track"
    assert result["image"] == "http://ma/image"
    assert result["artists"] == [{"name": "Artist", "uri": "apple_music://artist/7"}]
    assert result["album"] == {"name": "Album", "uri": "apple_music://album/9"}


def test_players_respect_optional_allowlist(monkeypatch):
    monkeypatch.setattr(
        standalone.client,
        "command",
        lambda command, **kwargs: [
            {"player_id": "p2", "name": "Backyard"},
            {"player_id": "p1", "name": "Kitchen"},
        ],
    )
    monkeypatch.setattr(standalone, "PLAYER_ALLOWLIST", {"p1"})

    assert standalone.get_players() == [{"player_id": "p1", "name": "Kitchen"}]


def test_favorites_call_music_assistant_library_commands(monkeypatch):
    calls = []

    def fake_command(command, **kwargs):
        calls.append((command, kwargs))
        return [{"uri": f"library://{command}", "name": "Favorite"}]

    monkeypatch.setattr(standalone.client, "command", fake_command)

    result = standalone.favorites(limit=12)

    assert set(result) == {"artists", "albums", "tracks", "playlists", "radio"}
    assert len(calls) == 5
    assert all(call[1]["favorite"] is True for call in calls)
    assert all(call[1]["limit"] == 12 for call in calls)


def test_search_scopes_provider_domains(monkeypatch):
    captured = {}

    def fake_command(command, **kwargs):
        captured["command"] = command
        captured["args"] = kwargs
        return {"tracks": [{"uri": "spotify://track/1", "name": "Track"}]}

    monkeypatch.setattr(standalone.client, "command", fake_command)

    result = standalone.search("test", provider="spotify", limit=7)

    assert captured["command"] == "music/search"
    assert captured["args"]["providers"] == ["spotify"]
    assert captured["args"]["limit"] == 7
    assert result["tracks"][0]["uri"] == "spotify://track/1"


def test_pwa_keeps_token_out_of_browser_assets():
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    index = (ROOT / "standalone" / "web" / "index.html").read_text()

    assert "MA_TOKEN" not in app_js
    assert "Authorization" not in app_js
    assert "MA_TOKEN" not in index
    assert "/api/play" in app_js
    assert "/api/transport" in app_js
    assert "/api/volume" in app_js
    assert "/api/mute" in app_js


def test_pwa_is_installable_and_has_immediate_feedback():
    index = (ROOT / "standalone" / "web" / "index.html").read_text()
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    manifest = (ROOT / "standalone" / "web" / "manifest.webmanifest").read_text()

    assert 'rel="manifest"' in index
    assert '"display": "standalone"' in manifest
    assert 'className = "feedback"' in app_js
    assert "Starting…" in app_js
    assert 'classList.add("accepted")' in app_js



def test_haos_app_package_keeps_home_assistant_out_of_runtime_path():
    repo_config = (ROOT / "repository.yaml").read_text()
    app_config = (ROOT / "standalone" / "config.yaml").read_text()
    dockerfile = (ROOT / "standalone" / "Dockerfile").read_text()
    run_script = (ROOT / "standalone" / "run.sh").read_text()

    assert "name: Family Music" in repo_config
    assert 'version: "0.2.4"' in app_config
    assert "host_network: true" in app_config
    assert "http://127.0.0.1:8095" in app_config
    assert "ma_token: password" in app_config
    assert "homeassistant_api" not in app_config
    assert "hassio_api" not in app_config
    assert "FROM python:3.13-alpine" in dockerfile
    assert "BUILD_FROM" not in dockerfile
    assert "MA_URL" in run_script
    assert "MA_TOKEN" in run_script



def test_haos_app_webui_uses_supervisor_placeholders():
    app_config = (ROOT / "standalone" / "config.yaml").read_text()
    assert 'webui: "[PROTO:http]://[HOST]:[PORT:8099]"' in app_config
    assert "webui: http://[HOST]:8099" not in app_config


def test_standalone_picker_is_not_rebuilt_on_every_poll():
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()

    assert "renderDestinationOverlay" in app_js
    assert "localStorage.setItem(\"family-music-player-id\"" in app_js


def test_standalone_mute_is_interactive_and_optimistic():
    index = (ROOT / "standalone" / "web" / "index.html").read_text()
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    server = (ROOT / "standalone" / "server.py").read_text()

    assert 'id="mute"' in index
    assert "pendingMute" in app_js
    assert 'addEventListener("click",toggleMute)' in app_js
    assert 'path == "/api/mute"' in server
    assert '"players/cmd/volume_mute"' in server


def test_standalone_card_parity_controls():
    index = (ROOT / "standalone" / "web" / "index.html").read_text()
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    server = (ROOT / "standalone" / "server.py").read_text()

    for element_id in (
        "progress",
        "shuffle",
        "repeat",
        "volumeDown",
        "volumeUp",
        "destination",
        "more",
        "openSearch",
    ):
        assert f'id="{element_id}"' in index
    assert 'player?.type === "group" ? player?.group_volume : player?.volume_level' in app_js
    assert 'renderDestinationOverlay' in app_js
    assert 'renderBalanceOverlay' in app_js
    assert 'path == "/api/queue-control"' in server
    assert '"player_queues/seek"' in server
    assert '"player_queues/shuffle"' in server
    assert '"player_queues/repeat"' in server


def test_standalone_primary_tabbar_is_present():
    index = (ROOT / "standalone" / "web" / "index.html").read_text()
    styles = (ROOT / "standalone" / "web" / "styles.css").read_text()
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    for view in ("now", "recents", "favorites", "search"):
        assert ('data-view="' + view + '"') in index
    assert 'class="tabbar"' in index
    assert ".tabbar{" in styles
    assert index.count('class="tabbar"') == 1
    assert "button.dataset.view" in app_js


def test_standalone_tabbar_matches_compact_shell_width():
    styles = (ROOT / "standalone" / "web" / "styles.css").read_text()
    assert ".app-shell{width:min(100%,408px)" in styles
    assert ".tabbar{position:fixed;left:50%;bottom:0;transform:translateX(-50%);width:min(100%,408px)" in styles
