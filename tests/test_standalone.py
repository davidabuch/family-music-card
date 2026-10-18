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
    assert len(calls) == 6
    assert all(call[1]["favorite"] is True for call in calls[:5])
    assert all(call[1]["limit"] == 12 for call in calls[:5])
    assert calls[5][0] == "music/playlists/library_items"
    assert "favorite" not in calls[5][1]


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
    assert 'version: "0.2.42"' in app_config
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


def test_transport_mode_controls_have_correct_order_and_native_routes():
    index = (ROOT / "standalone/web/index.html").read_text()
    js = (ROOT / "standalone/web/app.js").read_text()
    server = (ROOT / "standalone/server.py").read_text()
    assert index.index('id="repeat"') < index.index('id="previous"')
    assert index.index('id="next"') < index.index('id="shuffle"')
    assert 'current === "off" ? "one"' in js
    assert '"/api/native-mode-control"' in js
    assert '"/api/native-modes"' in server
    assert '"can_previous"' in server


def test_native_mode_mapping_preserves_repeat_and_shuffle(monkeypatch):
    import sys
    import types

    class Speaker:
        uid = "p1"
        play_mode = "NORMAL"

    speaker = Speaker()
    discovery = types.ModuleType("soco.discovery")
    discovery.discover = lambda timeout: {speaker}
    monkeypatch.setitem(sys.modules, "soco.discovery", discovery)
    standalone.set_sonos_playback_mode("p1", "repeat", "one")
    assert speaker.play_mode == "REPEAT_ONE"
    standalone.set_sonos_playback_mode("p1", "shuffle", True)
    assert speaker.play_mode == "SHUFFLE_REPEAT_ONE"
    standalone.set_sonos_playback_mode("p1", "repeat", "all")
    assert speaker.play_mode == "SHUFFLE"
    standalone.set_sonos_playback_mode("p1", "repeat", "off")
    assert speaker.play_mode == "SHUFFLE_NOREPEAT"


def test_remote_artwork_proxy_rejects_untrusted_paths(monkeypatch):
    """Image proxy never accepts a caller-selected upstream origin."""
    import io

    handler = object.__new__(standalone.Handler)
    handler.wfile = io.BytesIO()
    handler.path = "/api/artwork"
    responses = []
    handler._send_json = lambda payload, status=200: responses.append((payload, status))

    for path in ("https://evil.example/imageproxy", "//evil.example/imageproxy",
                 "/api/state", "/imageproxy.evil", "/../admin"):
        handler._query = lambda path=path: {"path": [path]}
        handler._serve_artwork()
        assert responses[-1][1] == 400


def test_remote_artwork_proxy_is_same_origin_and_bounded():
    app_js = (ROOT / "standalone/web/app.js").read_text()
    server = (ROOT / "standalone/server.py").read_text()
    assert '"/api/artwork?path="' in app_js
    assert '"/api/artwork"' in server
    assert "origin + path" in server
    assert '"/api/sonos-artwork"' in server
    assert "NoRedirect" in server
    assert "5 * 1024 * 1024" in server


def test_music_assistant_image_objects_use_proxy_id():
    """Now-playing image objects and search list artwork use the same proxy."""
    app_js = (ROOT / "standalone/web/app.js").read_text()
    assert 'typeof image.proxy_id === "string"' in app_js
    assert '"/imageproxy/" + image.proxy_id' in app_js
    assert "item?.image," in app_js
    assert "const imageUrl = mediaImage(item);" in app_js


def test_sonos_station_artwork_fallback():
    js = (ROOT / "standalone/web/app.js").read_text()
    assert "recoverSonosArtwork" in js
    assert 'image.startsWith("/api/sonos-artwork?")' in js
    assert 'provider: "apple"' in js
    assert "norm(entry.name) === norm(title)" in js
    assert "imageNode.isConnected" in js


def test_native_sonos_artwork_resolved_server_side():
    import importlib.util
    import unittest.mock as mock

    spec = importlib.util.spec_from_file_location(
        "family_music_server_art", ROOT / "standalone/server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    players = [{
        "current_media": {
            "title": "Heaven",
            "artist": "John Legend",
            "image_url": "http://192.168.1.17:1400/getaa?s=1",
        }
    }]
    tracks = [{
        "name": "Heaven",
        "artists": [{"name": "John Legend"}],
        "image": "https://example.com/art.jpg",
    }]
    with mock.patch.object(module, "search", return_value={"tracks": tracks}):
        module.resolve_sonos_artwork(players)
    assert players[0]["current_media"]["image_url"] == "https://example.com/art.jpg"


def test_dynamic_sonos_grouping_uses_music_assistant_native_group_commands(monkeypatch):
    calls = []
    players = [
        {
            "player_id": "RINCON_KITCHEN",
            "name": "Kitchen",
            "provider": "sonos",
            "type": "player",
            "available": True,
            "synced_to": None,
            "group_members": [],
            "group_childs": [],
            "can_group_with": ["RINCON_BEDROOM"],
        },
        {
            "player_id": "RINCON_BEDROOM",
            "name": "Bedroom",
            "provider": "sonos",
            "type": "player",
            "available": True,
            "synced_to": None,
            "group_members": [],
            "group_childs": [],
            "can_group_with": ["RINCON_KITCHEN"],
        },
    ]
    monkeypatch.setattr(standalone, "get_players", lambda: players)
    monkeypatch.setattr(
        standalone.client,
        "command",
        lambda command, **kwargs: calls.append((command, kwargs)) or None,
    )

    result = standalone.change_sonos_group(
        "RINCON_KITCHEN", "RINCON_BEDROOM", "join"
    )

    assert result["accepted"] is True
    assert calls == [(
        "players/cmd/group_many",
        {
            "target_player": "RINCON_KITCHEN",
            "child_player_ids": ["RINCON_BEDROOM"],
        },
    )]


def test_dynamic_sonos_grouping_removes_only_requested_member(monkeypatch):
    calls = []
    players = [
        {
            "player_id": "RINCON_KITCHEN",
            "provider": "sonos",
            "type": "player",
            "available": True,
            "group_members": ["RINCON_KITCHEN", "RINCON_BEDROOM"],
            "group_childs": ["RINCON_BEDROOM"],
            "can_group_with": ["RINCON_BEDROOM"],
        },
        {
            "player_id": "RINCON_BEDROOM",
            "provider": "sonos",
            "type": "player",
            "available": True,
            "synced_to": "RINCON_KITCHEN",
            "group_members": [],
            "group_childs": [],
        },
    ]
    monkeypatch.setattr(standalone, "get_players", lambda: players)
    monkeypatch.setattr(
        standalone.client,
        "command",
        lambda command, **kwargs: calls.append((command, kwargs)) or None,
    )

    result = standalone.change_sonos_group(
        "RINCON_KITCHEN", "RINCON_BEDROOM", "leave"
    )

    assert result["accepted"] is True
    assert calls == [("players/cmd/ungroup", {"player_id": "RINCON_BEDROOM"})]


def test_dynamic_sonos_grouping_refuses_preset_group_mutation(monkeypatch):
    monkeypatch.setattr(
        standalone,
        "get_players",
        lambda: [{
            "player_id": "syncgroup_main",
            "provider": "sync_group",
            "type": "group",
            "available": True,
            "group_members": ["RINCON_KITCHEN"],
        }],
    )
    import pytest

    with pytest.raises(ValueError, match="physical Sonos"):
        standalone.change_sonos_group(
            "syncgroup_main", "RINCON_BEDROOM", "join"
        )


def test_dynamic_grouping_ui_is_optimistic_but_waits_for_authoritative_state():
    js = (ROOT / "standalone" / "web" / "app.js").read_text()
    css = (ROOT / "standalone" / "web" / "styles.css").read_text()

    assert '"/api/group-member"' in js
    assert "Group Speakers" in js
    assert "Joining…" in js
    assert "Leaving…" in js
    assert "authoritativeGroupMembership" in js
    assert "Sonos group change was not confirmed" in js
    assert 'provider === "sonos"' in js
    assert ".group-speaker-option.pending" in css


def test_dynamic_group_can_remove_coordinator_and_follow_successor(monkeypatch):
    calls = []
    observations = iter([
        [
            {
                "player_id": "RINCON_MASTER",
                "provider": "sonos",
                "type": "player",
                "available": True,
                "group_members": ["RINCON_MASTER", "RINCON_KITCHEN"],
                "group_childs": ["RINCON_MASTER", "RINCON_KITCHEN"],
                "can_group_with": ["RINCON_KITCHEN"],
            },
            {
                "player_id": "RINCON_KITCHEN",
                "provider": "sonos",
                "type": "player",
                "available": True,
                "synced_to": "RINCON_MASTER",
                "group_members": [],
                "group_childs": [],
                "can_group_with": ["RINCON_MASTER"],
            },
        ],
        [
            {
                "player_id": "RINCON_MASTER",
                "provider": "sonos",
                "type": "player",
                "available": True,
                "synced_to": "RINCON_KITCHEN",
                "group_members": [],
                "group_childs": [],
            },
            {
                "player_id": "RINCON_KITCHEN",
                "provider": "sonos",
                "type": "player",
                "available": True,
                "group_members": ["RINCON_KITCHEN"],
                "group_childs": ["RINCON_KITCHEN"],
            },
        ],
    ])
    monkeypatch.setattr(standalone, "get_players", lambda: next(observations))
    monkeypatch.setattr(
        standalone.client,
        "command",
        lambda command, **kwargs: calls.append((command, kwargs)) or None,
    )

    result = standalone.change_sonos_group(
        "RINCON_MASTER", "RINCON_MASTER", "leave"
    )

    assert calls == [("players/cmd/ungroup", {"player_id": "RINCON_MASTER"})]
    assert result["selected_player_id"] == "RINCON_KITCHEN"
    assert result["leader_id"] == "RINCON_KITCHEN"


def test_dynamic_group_ui_has_member_balance_and_coordinator_removal():
    js = (ROOT / "standalone" / "web" / "app.js").read_text()
    css = (ROOT / "standalone" / "web" / "styles.css").read_text()

    assert "groupBalanceMarkup" in js
    assert "wireMemberTrimControls" in js
    assert "Speaker Balance" in js
    assert "without switching speakers" in js
    assert "Current playback · tap to remove" in js
    assert "selected_player_id" in js
    assert "family-music-player-id" in js
    assert ".group-balance-section" in css


def test_dynamic_group_popup_waits_for_authoritative_membership_without_reopen():
    js = (ROOT / "standalone" / "web" / "app.js").read_text()

    assert "waitForGroupMembership" in js
    assert "for (let attempt = 0; attempt < 10; attempt += 1)" in js
    assert "authoritativeGroupMembership(memberId) === expected" in js
    assert "if (state.groupOpen) renderGroupOverlay()" in js


def test_now_playing_uses_descriptive_speaker_icons():
    index = (ROOT / "standalone" / "web" / "index.html").read_text()

    assert 'class="speaker-group-icon"' in index
    assert 'class="single-speaker-icon"' in index
    assert 'aria-label="Choose playback destination"' in index
    assert 'aria-label="Group speakers"' in index
    assert '<span class="speaker-icon">▣</span>' not in index
    assert 'aria-label="Speaker balance">▣</button>' not in index


def test_mute_moves_out_of_volume_row_into_bottom_actions():
    index = (ROOT / "standalone" / "web" / "index.html").read_text()
    styles = (ROOT / "standalone" / "web" / "styles.css").read_text()

    volume = index.split('<div class="volume-row">', 1)[1].split("</div>", 1)[0]
    bottom = index.split('<div class="bottom-actions">', 1)[1].split("</div>", 1)[0]
    assert 'id="mute"' not in volume
    assert bottom.index('id="mute"') < bottom.index('id="openSearch"')
    assert bottom.index('id="openSearch"') < bottom.index('id="destination"')
    assert bottom.index('id="destination"') < bottom.index('id="more"')
    assert "grid-template-columns:46px 46px minmax(0,1fr) 46px" in styles


def test_state_path_is_provider_free_and_request_bodies_are_bounded():
    import io

    import pytest

    server_text = SERVER_PATH.read_text()
    state_block = server_text.split('if path == "/api/state":', 1)[1].split(
        'if path == "/api/native-modes":', 1
    )[0]
    assert "resolve_sonos_artwork" not in state_block
    assert standalone.MAX_JSON_BODY_BYTES == 64 * 1024
    assert standalone.MAX_CONCURRENT_REQUESTS == 6

    handler = object.__new__(standalone.Handler)
    handler.headers = {"Content-Length": str(standalone.MAX_JSON_BODY_BYTES + 1)}
    handler.rfile = io.BytesIO(b"")
    with pytest.raises(ValueError, match="64 KiB"):
        handler._read_json()


def test_polling_is_single_flight_with_backoff_and_no_silent_room_fallback():
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    assert "if (state.refreshPromise) return state.refreshPromise" in app_js
    assert "state.refreshPromise = null" in app_js
    assert "setInterval(refreshState" not in app_js
    assert "schedulePoll(nextDelay)" in app_js
    assert "Math.min(12000, 1500 * (2 ** state.pollFailures))" in app_js
    select_block = app_js.split("function renderPlayerSelect()", 1)[1].split(
        "function destinationIsPlaying", 1
    )[0]
    assert "some((p) => p.player_id === state.selectedPlayerId)" not in select_block
    assert '"Speaker unavailable"' in app_js


def test_volume_writes_are_serialized_and_latest_value_coalesced():
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    assert "volumeWrites: new Map()" in app_js
    assert "if (writer.inFlight || writer.pendingLevel === null) return" in app_js
    assert "writer.pendingLevel = bounded" in app_js
    assert "writer.inFlight = true" in app_js
    assert "writer.inFlight = false" in app_js
    assert "queueVolumeWrite(player.player_id, bounded, flush)" in app_js


def test_sonos_artwork_fallback_is_cached_outside_hot_state_poll():
    app_js = (ROOT / "standalone" / "web" / "app.js").read_text()
    assert "artworkFallbackCache: new Map()" in app_js
    assert "state.artworkFallbackCache.get(cacheKey)" in app_js
    assert "state.artworkFallbackCache.set(cacheKey, lookup)" in app_js
    assert "state.artworkFallbackCache.size > 100" in app_js


def test_queue_and_mutation_scope_enforce_player_allowlist(monkeypatch):
    players = [
        {"player_id": "allowed", "active_source": "allowed_queue", "name": "Allowed"},
    ]
    monkeypatch.setattr(standalone, "PLAYER_ALLOWLIST", {"allowed"})
    monkeypatch.setattr(standalone, "get_players", lambda: players)
    monkeypatch.setattr(
        standalone.client,
        "command",
        lambda command, **kwargs: [
            {"queue_id": "allowed_queue"},
            {"queue_id": "excluded_queue"},
        ] if command == "player_queues/all" else [],
    )

    assert standalone.get_queues(players) == [{"queue_id": "allowed_queue"}]
    standalone._require_allowed_destination("allowed")
    standalone._require_allowed_destination("allowed_queue")

    import pytest

    with pytest.raises(standalone.AuthorizationError):
        standalone._require_allowed_destination("excluded")


def test_write_requests_require_json_and_reject_foreign_origin():
    handler = object.__new__(standalone.Handler)
    responses = []
    handler._send_json = lambda value, status=200: responses.append((value, status))

    handler.headers = {"Content-Type": "text/plain", "Host": "family.local:8099"}
    assert handler._validate_write_request() is False
    assert responses[-1][1] == 415

    handler.headers = {
        "Content-Type": "application/json",
        "Host": "family.local:8099",
        "Origin": "https://evil.example",
    }
    assert handler._validate_write_request() is False
    assert responses[-1][1] == 403

    handler.headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Host": "family.local:8099",
        "Origin": "http://family.local:8099",
    }
    assert handler._validate_write_request() is True

    handler.headers = {"Content-Type": "application/json", "Host": "family.local:8099"}
    assert handler._validate_write_request() is True


def test_state_filters_queues_when_allowlist_is_configured():
    server_text = SERVER_PATH.read_text()
    state_block = server_text.split('if path == "/api/state":', 1)[1].split(
        'if path == "/api/native-modes":', 1
    )[0]
    assert "get_queues(players)" in state_block


def test_server_exits_cleanly_on_supervisor_sigterm():
    import os
    import signal
    import subprocess
    import sys

    env = dict(os.environ, PORT="0")
    process = subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert "Family Music listening" in process.stdout.readline()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert "Family Music shutdown complete" in stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_normalize_item_preserves_provider_mappings_for_favorites():
    item = {
        "uri": "library://album/1",
        "name": "Album",
        "provider_mappings": [
            {"provider_instance": "apple_music--account"},
            {"provider_domain": "spotify"},
        ],
    }
    result = standalone.normalize_item(item)
    assert result["providers"] == ["apple_music", "spotify"]


def test_search_returns_separate_artist_album_and_track_collections(monkeypatch):
    def fake_command(command, **kwargs):
        assert command == "music/search"
        assert set(kwargs["media_types"]) == {"artist", "album", "track", "playlist", "radio"}
        return {
            "artists": [{"uri": "apple_music://artist/1", "name": "Ed Sheeran"}],
            "albums": [{"uri": "apple_music://album/2", "name": "Divide"}],
            "tracks": [{"uri": "apple_music://track/3", "name": "Shape of You"}],
        }
    monkeypatch.setattr(standalone.client, "command", fake_command)
    result = standalone.search("Ed Sheeran", provider="apple")
    assert [item["name"] for item in result["artists"]] == ["Ed Sheeran"]
    assert [item["name"] for item in result["albums"]] == ["Divide"]
    assert [item["name"] for item in result["tracks"]] == ["Shape of You"]


def test_group_sonos_favorite_resolves_single_exact_catalog_match(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def fake_command(command, **kwargs):
        calls.append((command, kwargs))
        return {"albums": [
            {"name": "Exact Album", "uri": "apple_music://album/7"},
            {"name": "Exact Album Deluxe", "uri": "spotify://album/8"},
        ]}
    monkeypatch.setattr(standalone.client, "command", fake_command)
    uri = standalone.resolve_group_sonos_favorite(
        SimpleNamespace(title="Exact Album", item_class="object.container.album.musicAlbum")
    )
    assert uri == "apple_music://album/7"
    assert calls[0][1]["media_types"] == ["album"]


def test_group_sonos_favorite_rejects_ambiguous_matches(monkeypatch):
    from types import SimpleNamespace

    import pytest
    monkeypatch.setattr(
        standalone.client, "command",
        lambda *args, **kwargs: {"tracks": [
            {"name": "Same Song", "uri": "spotify://track/1"},
            {"name": "Same Song", "uri": "apple_music://track/2"},
        ]},
    )
    with pytest.raises(ValueError, match="cannot be resolved uniquely"):
        standalone.resolve_group_sonos_favorite(
            SimpleNamespace(title="Same Song", item_class="object.item.audioItem.musicTrack")
        )


def test_group_sonos_favorite_never_guesses_when_missing(monkeypatch):
    from types import SimpleNamespace

    import pytest
    monkeypatch.setattr(standalone.client, "command", lambda *args, **kwargs: {})
    with pytest.raises(ValueError, match="cannot be resolved uniquely"):
        standalone.resolve_group_sonos_favorite(
            SimpleNamespace(title="Missing", item_class="object.item.audioItem.musicTrack")
        )


def test_normalize_item_preserves_ma_artwork_proxy_id():
    proxy_id = "510bac24e668fe214e46a77d08ed15f172079b4c899b373c4de1a813a9f3fba4"
    image_url = "https://is1-ssl.mzstatic.com/image/thumb/example.png/1000x1000.jpg"
    item = {"name": "Today's Hits", "metadata": {"images": [
        {"path": image_url, "proxy_id": proxy_id}
    ]}}
    assert standalone.normalize_item(item)["image"] == {
        "path": image_url, "proxy_id": proxy_id
    }


def test_normalize_item_rejects_invalid_artwork_proxy_id():
    item = {"metadata": {"images": [
        {"path": "https://example.com/image.jpg", "proxy_id": "invalid"}
    ]}}
    assert standalone.normalize_item(item)["image"] == "https://example.com/image.jpg"


def test_favorites_include_saved_provider_playlists_without_changing_flags(monkeypatch):
    def fake_command(command, **kwargs):
        if command != "music/playlists/library_items":
            return []
        if kwargs.get("favorite") is True:
            return [{"uri": "library://playlist/1", "name": "Favorite", "favorite": True}]
        return [
            {"uri": "library://playlist/1", "name": "Favorite", "favorite": True},
            {"uri": "library://playlist/9", "name": "Biking workout", "favorite": False,
             "provider_mappings": [{"provider_domain": "apple_music"}]},
            {"uri": "library://playlist/27", "name": "Top 40", "favorite": False,
             "provider_mappings": [{"provider_domain": "spotify"}]},
            {"uri": "library://playlist/99", "name": "Unrelated", "favorite": False,
             "provider_mappings": [{"provider_domain": "other"}]},
        ]

    monkeypatch.setattr(standalone.client, "command", fake_command)
    monkeypatch.setattr(standalone, "native_sonos_favorites", lambda: [])
    items = standalone.favorites()["playlists"]
    assert [item["uri"] for item in items] == [
        "library://playlist/1", "library://playlist/9", "library://playlist/27"
    ]
    assert items[1]["saved_library"] is True
    assert items[1]["favorite"] is False
    assert items[2]["saved_library"] is True
