"""Behavioral regression for the observed origin EOF / Cloudflare 502."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "standalone"))
spec = importlib.util.spec_from_file_location(
    "grouped_transport_server", ROOT / "standalone/server.py"
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def native_pair(monkeypatch, member_id="bedroom"):
    leader = SimpleNamespace(uid="kitchen", pause=Mock(), play=Mock(), is_coordinator=True)
    group = SimpleNamespace(coordinator=leader, members=[])
    leader.group = group
    member = SimpleNamespace(uid=member_id, group=group, is_coordinator=False)
    member.pause = Mock(side_effect=RuntimeError("SoCoSlaveException: coordinator only"))
    member.play = member.pause
    group.members = [leader, member]
    monkeypatch.setitem(sys.modules, "soco.discovery",
                        SimpleNamespace(discover=lambda timeout: {HashableSpeaker(member)}))
    monkeypatch.setattr(server, "get_queues", lambda: [])
    monkeypatch.setattr(server, "get_players", lambda: [
        {"player_id": member_id, "state": "playing", "synced_to": "kitchen"},
        {"player_id": "kitchen", "state": "playing"},
    ])
    return leader, member


class HashableSpeaker:
    def __init__(self, speaker):
        self.__dict__.update(speaker.__dict__)


@pytest.mark.parametrize("member_id", ["bedroom", "alexis"])
def test_pause_member_uses_verified_native_coordinator_once(monkeypatch, member_id):
    leader, member = native_pair(monkeypatch, member_id)
    assert server._native_sonos_transport(member_id, "pause") is True
    leader.pause.assert_called_once()
    member.pause.assert_not_called()


def test_native_failure_returns_json_instead_of_origin_eof(monkeypatch):
    handler = object.__new__(server.Handler)
    handler.path = "/api/transport"
    handler.headers = {"Content-Type": "application/json"}
    body = json.dumps({"queue_id": "bedroom", "action": "pause"}).encode()
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    handler._send_json = Mock()
    monkeypatch.setattr(server, "_require_allowed_destination", lambda *args: None)
    monkeypatch.setattr(server, "_native_sonos_transport",
                        Mock(side_effect=RuntimeError("coordinator only")))
    handler._do_POST()
    assert handler._send_json.call_args.args[1] == 502
    assert handler._send_json.call_args.args[0]["request_id"]


def test_resume_grouped_idle_member_uses_coordinator(monkeypatch):
    leader, member = native_pair(monkeypatch)
    monkeypatch.setattr(server, "get_players", lambda: [
        {"player_id": "bedroom", "state": "idle", "synced_to": "kitchen"},
        {"player_id": "kitchen", "state": "paused"},
    ])
    assert server._native_sonos_transport("bedroom", "play")
    leader.play.assert_called_once()
    member.play.assert_not_called()


def test_unverified_native_group_never_falls_back_to_arbitrary_speaker(monkeypatch):
    leader, member = native_pair(monkeypatch)
    member.group.coordinator = None
    with pytest.raises(ValueError, match="Cannot verify"):
        server._native_sonos_transport("bedroom", "pause")
    leader.pause.assert_not_called()
    member.pause.assert_not_called()


def test_native_observer_rejects_ambiguous_state_and_protects_permanent_ma_source(monkeypatch):
    import copy
    from types import ModuleType

    players = [
        {"player_id": "A", "provider": "sonos", "type": "player", "available": True,
         "state": "paused", "group_members": ["A", "B"],
         "device_info": {"ip_address": "192.0.2.1"}},
        {"player_id": "B", "provider": "sonos", "type": "player", "available": True,
         "state": "paused", "synced_to": "A", "device_info": {"ip_address": "192.0.2.2"}},
        {"player_id": "preset", "type": "group"},
    ]
    xml = '<ZoneGroups><ZoneGroup Coordinator="A"><ZoneGroupMember UUID="A"/>' \
          '<ZoneGroupMember UUID="B"/></ZoneGroup></ZoneGroups>'
    transport = {"CurrentTransportState": "PAUSED_PLAYBACK", "CurrentTransportStatus": "OK"}
    topology_service = SimpleNamespace(GetZoneGroupState=Mock(return_value={"ZoneGroupState": xml}))
    transport_service = SimpleNamespace(
        GetTransportInfo=Mock(side_effect=lambda *a, **k: transport)
    )
    speaker = SimpleNamespace(zoneGroupTopology=topology_service, avTransport=transport_service)
    soco = ModuleType("soco")
    soco.SoCo = lambda _ip: speaker
    monkeypatch.setitem(sys.modules, "soco", soco)
    monkeypatch.setattr(server.client, "command", lambda *_args, **_kwargs: copy.deepcopy(players))
    snapshot = server._cleanup_observation({"A", "B"})
    assert snapshot.matches(frozenset({"A", "B"}))
    assert snapshot.idle(frozenset({"A", "B"}))
    topology_service.GetZoneGroupState.assert_called_with([], timeout=2)
    transport["CurrentTransportState"] = "TRANSITIONING"
    assert not server._cleanup_observation({"A", "B"}).idle(frozenset({"A", "B"}))
    players[0]["active_source"] = "preset"
    assert not server._cleanup_observation({"A", "B"}).matches(frozenset({"A", "B"}))


def test_broken_diagnostic_sink_cannot_interrupt_playback(monkeypatch):
    leader, _member = native_pair(monkeypatch)
    monkeypatch.setattr(server._LOGGER, "warning", Mock(side_effect=RuntimeError("log sink")))
    assert server._native_sonos_transport("bedroom", "pause")
    leader.pause.assert_called_once()


def test_native_failure_is_real_http_json_not_dropped_connection(monkeypatch):
    import threading
    import urllib.error
    import urllib.request

    monkeypatch.setattr(server, "_require_allowed_destination", lambda *args: None)
    monkeypatch.setattr(server, "_native_sonos_transport",
                        Mock(side_effect=RuntimeError("coordinator only")))
    httpd = server.FamilyMusicHTTPServer(("127.0.0.1", 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}/api/transport",
            data=json.dumps({"queue_id": "bedroom", "action": "pause", "request_id": "test-98"})
            .encode(), headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        assert caught.value.code == 502
        assert caught.value.headers["X-Family-Music-Request-ID"] == "test-98"
        body = json.loads(caught.value.read())
        assert body["request_id"] == "test-98"
        assert body["outcome"] == "unknown"
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=2)
