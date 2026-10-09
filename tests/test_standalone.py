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
    monkeypatch.setattr(standalone, "native_sonos_favorites", lambda: [])

    result = standalone.favorites(limit=12)

    assert set(result) == {"artists", "albums", "tracks", "playlists", "radio", "sonos_favorites"}
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
    assert 'version: "0.2.18"' in app_config
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
    assert ".tabbar{position:fixed;left:50%;bottom:0" in styles
    assert "transform:translateX(-50%);width:min(100%,408px)" in styles


def test_standalone_tabbar_hides_while_modal_overlays_are_open():
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    styles = (ROOT / "standalone" / "web" / "styles.css").read_text()
    assert 'classList.add("overlay-hidden")' in app_js
    assert 'classList.remove("overlay-hidden")' in app_js
    assert ".tabbar.overlay-hidden{" in styles
    assert "pointer-events:none" in styles


def test_standalone_destination_playing_equalizer():
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    styles = (ROOT / "standalone" / "web" / "styles.css").read_text()
    assert "function destinationIsPlaying(player)" in app_js
    assert "function updateDestinationActivity()" in app_js
    assert 'class="playing-equalizer"' in app_js
    assert ".playing-equalizer{" in styles
    assert "@keyframes familyMusicEq" in styles
    assert 'document.querySelectorAll(".destination-option[data-player]")' in app_js


def test_native_sonos_favorites_are_resolved_and_deduplicated(monkeypatch):
    calls = []

    def command(name, **kwargs):
        calls.append(name)
        if name == "music/search":
            return {"playlists": [{"name": "Family Mix", "uri": "spotify://playlist/1"}]}
        if name == "music/playlists/library_items":
            return [{"name": "Existing", "uri": "spotify://playlist/2"}]
        return []

    monkeypatch.setattr(standalone.client, "command", command)
    monkeypatch.setattr(standalone, "native_sonos_favorites", lambda: [
        {"name": "Family Mix", "media_type": "playlist"},
        {"name": "Family Mix", "media_type": "playlist"},
        {"name": "Existing", "media_type": "playlist"},
    ])
    result = standalone.favorites()
    assert {item["uri"] for item in result["playlists"]} == {"spotify://playlist/2"}
    assert len(result["sonos_favorites"]) == 3


def test_sonos_favorites_are_visible_even_without_ma_match(monkeypatch):
    monkeypatch.setattr(standalone.client, "command", lambda *args, **kwargs: [])
    monkeypatch.setattr(standalone, "native_sonos_favorites", lambda: [
        {"name": "Sonos-only Station", "uri": "sonos-favorite://0"}
    ])
    result = standalone.favorites()
    assert result["sonos_favorites"][0]["name"] == "Sonos-only Station"


def test_sonos_favorite_index_preserved(monkeypatch):
    from types import SimpleNamespace

    entries = [
        SimpleNamespace(title="First", item_class="", album_art_uri=None),
        SimpleNamespace(title="", item_class="", album_art_uri=None),
        SimpleNamespace(title="Third", item_class="", album_art_uri=None),
    ]
    speaker = SimpleNamespace(
        music_library=SimpleNamespace(get_sonos_favorites=lambda: entries))
    monkeypatch.setattr(standalone, "_sonos_household", lambda: speaker)
    result = standalone.native_sonos_favorites()
    assert [item["uri"] for item in result] == [
        "sonos-favorite://0", "sonos-favorite://2"
    ]


def test_now_playing_declares_item_outside_comment():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "standalone/web/app.js").read_text()
    start = js.index("function renderNow()")
    end = js.index('$("#trackTitle")', start)
    block = js[start:end]
    assert '\\n' not in block
    assert '  const item = queueActive' in block.splitlines()


def test_native_sonos_radio_uses_reference_metadata():
    from types import SimpleNamespace
    calls = []

    class Speaker:
        def music_source_from_uri(self, uri):
            return "RADIO"

        def play_uri(self, uri, **kwargs):
            calls.append((uri, kwargs))

    favorite = SimpleNamespace(
        title="Station",
        resource_meta_data="<DIDL-Lite>radio</DIDL-Lite>",
        reference=SimpleNamespace(
            get_uri=lambda: "x-sonosapi-radio:station",
            item_class="object.item.audioItem.audioBroadcast",
        ),
    )
    standalone.play_sonos_favorite(Speaker(), favorite)
    assert calls == [(
        "x-sonosapi-radio:station",
        {"title": "Station", "meta": "<DIDL-Lite>radio</DIDL-Lite>"},
    )]


def test_native_sonos_playlist_queues_reference():
    from types import SimpleNamespace
    calls = []

    class Speaker:
        def music_source_from_uri(self, uri):
            return "LIBRARY"

        def clear_queue(self):
            calls.append("clear")

        def add_to_queue(self, reference):
            calls.append(("add", reference))

        def play_from_queue(self, index):
            calls.append(("play", index))

    reference = SimpleNamespace(
        get_uri=lambda: "x-rincon-cpcontainer:playlist",
        item_class="object.container.playlistContainer",
    )
    favorite = SimpleNamespace(reference=reference)
    standalone.play_sonos_favorite(Speaker(), favorite)
    assert calls == ["clear", ("add", reference), ("play", 0)]


def test_search_voice_control_accessibility():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "standalone/web"
    html = (root / "index.html").read_text()
    js = (root / "app.js").read_text()
    assert 'id="voiceSearch"' in html
    assert 'aria-label="Search by voice"' in html
    assert 'for="searchInput"' in html
    assert 'id="voiceStatus"' in html
    assert 'window.webkitSpeechRecognition' in js
    assert '!window.isSecureContext' in js
    assert 'input.focus()' in js
    assert 'recognition.onend' in js


def test_transport_uses_physical_player_and_native_progress():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    js = (root / "standalone/web/app.js").read_text()
    server = (root / "standalone/server.py").read_text()
    assert 'transport(selectedPlayer()?.state==="playing"?"pause":"play"' in js
    assert 'currentMediaPosition(item, player)' in js
    assert 'item?.elapsed_time_last_updated' in js
    assert 'action == "play" and player and player.get("state") == "idle"' in server


def test_native_previous_unsupported_transition_falls_back_to_ma(monkeypatch):
    import sys
    from types import SimpleNamespace

    class UnsupportedTransition(Exception):
        error_code = 701

    class Speaker:
        uid = "RINCON_TEST"

        def previous(self):
            raise UnsupportedTransition("Transition not available")

    monkeypatch.setattr(
        standalone, "get_queues",
        lambda: [{"queue_id": "RINCON_TEST", "state": "idle"}],
    )
    monkeypatch.setattr(
        standalone, "get_players",
        lambda: [{"player_id": "RINCON_TEST", "state": "playing"}],
    )
    monkeypatch.setitem(
        sys.modules, "soco.discovery",
        SimpleNamespace(discover=lambda timeout: {Speaker()}),
    )
    standalone.NATIVE_SONOS_SESSIONS.add("RINCON_TEST")
    try:
        assert standalone._native_sonos_transport("RINCON_TEST", "previous") is False
        assert "RINCON_TEST" not in standalone.NATIVE_SONOS_SESSIONS
    finally:
        standalone.NATIVE_SONOS_SESSIONS.discard("RINCON_TEST")


def test_previous_transport_restarts_active_ma_track(monkeypatch):
    calls = []
    monkeypatch.setattr(
        standalone, "get_queues",
        lambda: [{"queue_id": "p1", "state": "playing", "elapsed_time": 31}],
    )
    monkeypatch.setattr(standalone, "get_players", lambda: [{"player_id": "p1"}])
    monkeypatch.setattr(standalone, "NATIVE_SONOS_SESSIONS", set())
    monkeypatch.setattr(
        standalone.client, "command", lambda name, **kwargs: calls.append((name, kwargs))
    )
    result = standalone.previous_transport("p1")
    assert result["restarted"] is True
    assert calls == [("player_queues/seek", {"queue_id": "p1", "position": 0})]


def test_previous_transport_navigates_at_start_of_ma_track(monkeypatch):
    calls = []
    monkeypatch.setattr(
        standalone, "get_queues",
        lambda: [{"queue_id": "p1", "state": "paused", "elapsed_time": 2}],
    )
    monkeypatch.setattr(standalone, "get_players", lambda: [{"player_id": "p1"}])
    monkeypatch.setattr(standalone, "NATIVE_SONOS_SESSIONS", set())
    monkeypatch.setattr(standalone.client, "command", lambda name, **kwargs: calls.append(name))
    result = standalone.previous_transport("p1")
    assert result["restarted"] is False
    assert calls == ["player_queues/previous"]


def test_native_previous_uses_physical_position_not_zeroed_media_elapsed(monkeypatch):
    import sys
    import types

    class Speaker:
        uid = "p1"
        def __init__(self):
            self.seeks = []
            self.previous_calls = 0
        def get_current_track_info(self):
            return {"position": "0:01:22"}
        def seek(self, position):
            self.seeks.append(position)
        def previous(self):
            self.previous_calls += 1

    speaker = Speaker()
    soco = types.ModuleType("soco")
    discovery = types.ModuleType("soco.discovery")
    discovery.discover = lambda timeout: {speaker}
    monkeypatch.setitem(sys.modules, "soco", soco)
    monkeypatch.setitem(sys.modules, "soco.discovery", discovery)
    monkeypatch.setattr(standalone, "get_queues", lambda: [])
    monkeypatch.setattr(
        standalone, "get_players",
        lambda: [{"player_id": "p1", "elapsed_time": 0,
                  "current_media": {"elapsed_time": 0}}],
    )
    result = standalone.previous_transport("p1")
    assert result["restarted"] is True
    assert speaker.seeks == ["0:00:00"]
    assert speaker.previous_calls == 0


def test_previous_uses_sonos_group_coordinator(monkeypatch):
    import sys
    import types

    class Coordinator:
        def __init__(self):
            self.seeks = []
        def get_current_track_info(self):
            return {"position": "0:00:45"}
        def seek(self, position):
            self.seeks.append(position)

    coordinator = Coordinator()
    member = types.SimpleNamespace(
        uid="p1", group=types.SimpleNamespace(coordinator=coordinator)
    )
    discovery = types.ModuleType("soco.discovery")
    discovery.discover = lambda timeout: {member}
    monkeypatch.setitem(sys.modules, "soco.discovery", discovery)
    monkeypatch.setattr(standalone, "get_queues", lambda: [])
    monkeypatch.setattr(standalone, "get_players", lambda: [])
    result = standalone.previous_transport("p1")
    assert result["restarted"] is True
    assert coordinator.seeks == ["0:00:00"]
