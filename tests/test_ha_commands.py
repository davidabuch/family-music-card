"""Execute HA command bodies with injected HA boundaries, without an HA install.

Decorators are removed only to call the underlying coroutine directly; the
production setup registration path is exercised as well. No source-string
assertions stand in for permission, entry selection or diagnostic behavior.
"""

import ast
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

SOURCE = Path(__file__).resolve().parents[1] / "custom_components/family_music/__init__.py"


def functions(*names):
    tree = ast.parse(SOURCE.read_text())
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in selected:
        node.decorator_list = []
    namespace = {"HomeAssistant": object, "ConfigEntry": object, "DOMAIN": "family_music",
                 "VERSION": "0.3.29", "Path": Path, "__file__": str(SOURCE),
                 "_LOGGER": logging.getLogger("family_music.test"),
                 "time": SimpleNamespace(monotonic=lambda: 100),
                 "_require_entity_permission": lambda *args, **kwargs: True}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace


def test_diagnostic_is_callable_for_ma_entry_and_bounded(caplog):
    ns = functions("ws_client_diagnostic")
    hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(
        async_get_entry=lambda key: SimpleNamespace(domain="music_assistant")
        if key == "ma-entry" else None))
    connection = SimpleNamespace(send_result=Mock(), send_error=Mock())
    msg = {"id": 1, "config_entry_id": "ma-entry", "phase": "service_start",
           "selected": "media_player.bedroom", "target": "media_player.kitchen",
           "correlation_id": "one-action", "version": "0.3.29"}
    asyncio.run(ns["ws_client_diagnostic"](hass, connection, msg))
    assert connection.send_result.call_args.args[1] == {
        "recorded": True, "backend_version": "0.3.29"}
    assert "one-action" in caplog.text
    assert "media_player.kitchen" in caplog.text
    for _ in range(65):
        asyncio.run(ns["ws_client_diagnostic"](hass, connection, msg))
    assert connection.send_result.call_args.args[1]["recorded"] is False
    assert len(hass.data["family_music"]["diagnostic_rate"]) == 2
    asyncio.run(ns["ws_client_diagnostic"](hass, connection, {**msg, "config_entry_id": "wrong"}))
    assert connection.send_error.call_args.args[1] == "invalid_entry"


def test_diagnostic_denies_unreadable_selected_entity():
    ns = functions("ws_client_diagnostic")
    ns["_require_entity_permission"] = lambda *a, **k: False
    hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(
        async_get_entry=lambda key: SimpleNamespace(domain="music_assistant")))
    connection = SimpleNamespace(send_result=Mock())
    asyncio.run(ns["ws_client_diagnostic"](hass, connection, {
        "id": 1, "config_entry_id": "ma-entry", "phase": "button_click",
        "selected": "media_player.private"}))
    connection.send_result.assert_not_called()
    assert not hass.data


def test_setup_registers_callable_diagnostic_command_once():
    ns = functions("async_setup_entry", "ws_client_diagnostic")
    register = Mock()
    ns.update({"websocket_api": SimpleNamespace(async_register_command=register),
               "StaticPathConfig": Mock(), "CARD_PATH": "www/family-music-card.js",
               "CARD_URL": "/family_music/family-music-card.js",
               "_async_register_card_resource": AsyncMock()})
    for name in ("ws_search", "ws_group_members", "ws_player_peers", "ws_group_topology",
                 "ws_group_member", "ws_recents", "ws_favorites", "ws_artist_albums",
                 "ws_album_tracks"):
        ns[name] = Mock()
    hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock()))
    assert asyncio.run(ns["async_setup_entry"](hass, SimpleNamespace()))
    assert asyncio.run(ns["async_setup_entry"](hass, SimpleNamespace()))
    handlers = [call.args[1] for call in register.call_args_list]
    assert handlers.count(ns["ws_client_diagnostic"]) == 1
    assert asyncio.iscoroutinefunction(ns["ws_client_diagnostic"])


def test_ma_client_uses_loaded_music_assistant_entry_not_family_music_entry():
    ns = functions("_music_assistant_client")
    client = object()
    get = Mock(return_value=client)
    ns["get_music_assistant_client"] = get
    hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain: [
        SimpleNamespace(entry_id="ma-entry", state=SimpleNamespace(value="loaded"))]))
    assert ns["_music_assistant_client"](hass) is client
    get.assert_called_once_with(hass, "ma-entry")
