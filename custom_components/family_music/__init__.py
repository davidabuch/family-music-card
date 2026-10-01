"""Family Music integration setup and Music Assistant proxy commands."""

from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.music_assistant.helpers import get_music_assistant_client
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CARD_PATH, CARD_URL, DOMAIN, VERSION


def _provider_list(provider: str) -> list[str]:
    """Map the card provider choice to Music Assistant provider domains."""
    if provider == "apple":
        return ["apple_music"]
    if provider == "spotify":
        return ["spotify"]
    return ["apple_music", "spotify"]


@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/search",
        vol.Required("config_entry_id"): str,
        vol.Required("query"): str,
        vol.Optional("provider", default="all"): vol.In(("all", "apple", "spotify")),
        vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }
)
@websocket_api.async_response
async def ws_search(hass: HomeAssistant, connection, msg: dict) -> None:
    """Search the selected Music Assistant providers without exposing an MA token."""
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    result = await mass.send_command(
        "music/search",
        search_query=msg["query"],
        media_types=["artist", "album", "track", "playlist", "radio"],
        limit=msg["limit"],
        providers=_provider_list(msg["provider"]),
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/artist_albums",
        vol.Required("config_entry_id"): str,
        vol.Required("item_id"): str,
        vol.Required("provider"): str,
    }
)
@websocket_api.async_response
async def ws_artist_albums(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return the provider-native album listing for an artist."""
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    result = await mass.send_command(
        "music/artists/artist_albums",
        item_id=msg["item_id"],
        provider_instance_id_or_domain=msg["provider"],
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/album_tracks",
        vol.Required("config_entry_id"): str,
        vol.Required("item_id"): str,
        vol.Required("provider"): str,
    }
)
@websocket_api.async_response
async def ws_album_tracks(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return all tracks for a provider-native album."""
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    result = await mass.send_command(
        "music/albums/album_tracks",
        item_id=msg["item_id"],
        provider_instance_id_or_domain=msg["provider"],
    )
    connection.send_result(msg["id"], result)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Family Music."""
    if not hass.data.setdefault(DOMAIN, {}).get("registered"):
        card_path = Path(__file__).parent / CARD_PATH
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
        )
        frontend.add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
        websocket_api.async_register_command(hass, ws_search)
        websocket_api.async_register_command(hass, ws_artist_albums)
        websocket_api.async_register_command(hass, ws_album_tracks)
        hass.data[DOMAIN]["registered"] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Family Music."""
    return True
