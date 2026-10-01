"""Family Music integration setup and Music Assistant proxy commands."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.music_assistant.helpers import get_music_assistant_client
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CARD_PATH, CARD_URL, DOMAIN, VERSION


def _provider_list(provider: str) -> list[str]:
    """Map the card provider choice to Music Assistant provider domains."""
    if provider == "apple":
        return ["apple_music"]
    if provider == "spotify":
        return ["spotify"]
    return ["apple_music", "spotify"]


def _image(item: dict[str, Any]) -> str | None:
    """Return the first usable image URL from a Music Assistant item."""
    direct = item.get("image")
    if isinstance(direct, str) and direct:
        return direct
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return None
    images = metadata.get("images")
    if not isinstance(images, list):
        return None
    for image in images:
        if isinstance(image, dict) and isinstance(image.get("path"), str):
            return image["path"]
    return None


def _mapping(item: dict[str, Any]) -> dict[str, Any]:
    """Return a compact media item safe and convenient for the Lovelace card."""
    result: dict[str, Any] = {
        "media_type": item.get("media_type"),
        "uri": item.get("uri"),
        "name": item.get("name"),
        "version": item.get("version") or "",
        "image": _image(item),
        "favorite": bool(item.get("favorite", False)),
    }
    artists = item.get("artists")
    if isinstance(artists, list):
        result["artists"] = [
            {
                "media_type": artist.get("media_type", "artist"),
                "uri": artist.get("uri"),
                "name": artist.get("name"),
                "image": _image(artist),
            }
            for artist in artists
            if isinstance(artist, dict)
        ]
    album = item.get("album")
    if isinstance(album, dict):
        result["album"] = {
            "media_type": album.get("media_type", "album"),
            "uri": album.get("uri"),
            "name": album.get("name"),
            "image": _image(album),
        }
    for key in ("duration", "disc_number", "track_number", "year", "album_type"):
        if item.get(key) is not None:
            result[key] = item[key]
    return result


def _normalize_search(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Normalize Music Assistant search output for the frontend."""
    return {
        key: [_mapping(item) for item in result.get(key, []) if isinstance(item, dict)]
        for key in ("artists", "albums", "tracks", "playlists", "radio")
    }


def _name_tokens(name: str) -> set[str]:
    """Return normalized significant title tokens."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", name.casefold())
        if len(token) > 1
    }


def _browse_node(response: Any, entity_id: str) -> dict[str, Any]:
    """Extract one media browser node from a Home Assistant service response."""
    if not isinstance(response, dict):
        return {}
    node = response.get(entity_id)
    return node if isinstance(node, dict) else {}


async def _native_sonos_favorites(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Read household Sonos Favorites through Home Assistant's native Sonos browser."""
    registry = er.async_get(hass)
    candidates = [
        entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "media_player"
        and entry.platform == "sonos"
        and hass.states.get(entry.entity_id) is not None
    ]
    if not candidates:
        return []

    entity_id = sorted(candidates)[0]
    folders = (
        ("album", "object.container.album.musicAlbum"),
        ("playlist", "object.container.playlistContainer"),
        ("radio", "object.item.audioItem.audioBroadcast"),
    )
    favorites: list[dict[str, Any]] = []
    for media_type, folder_id in folders:
        response = await hass.services.async_call(
            "media_player",
            "browse_media",
            {
                "entity_id": entity_id,
                "media_content_type": "favorites_folder",
                "media_content_id": folder_id,
            },
            blocking=True,
            return_response=True,
        )
        node = _browse_node(response, entity_id)
        children = node.get("children")
        if not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict) or not child.get("can_play"):
                continue
            title = child.get("title")
            content_id = child.get("media_content_id")
            if not isinstance(title, str) or not isinstance(content_id, str):
                continue
            favorites.append(
                {
                    "name": title,
                    "media_type": media_type,
                    "image": child.get("thumbnail"),
                    "sonos_content_id": content_id,
                    "sonos_favorite": True,
                }
            )
    return favorites


def _best_existing_favorite(
    sonos_item: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Match a Sonos Favorite to an existing MA favorite."""
    wanted_name = str(sonos_item.get("name") or "").casefold().strip()
    wanted_tokens = _name_tokens(str(sonos_item.get("name") or ""))
    for candidate in candidates:
        name = str(candidate.get("name") or "")
        if name.casefold().strip() == wanted_name:
            return candidate
    if wanted_tokens:
        for candidate in candidates:
            candidate_tokens = _name_tokens(str(candidate.get("name") or ""))
            if wanted_tokens <= candidate_tokens:
                return candidate
    return None


async def _resolve_sonos_favorite(
    mass,
    sonos_item: dict[str, Any],
    existing: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a Sonos Favorite to a Music Assistant playable item."""
    if match := _best_existing_favorite(sonos_item, existing):
        return {**match, "sonos_favorite": True}

    media_type = str(sonos_item["media_type"])
    key = "radio" if media_type == "radio" else f"{media_type}s"
    result = await mass.send_command(
        "music/search",
        search_query=sonos_item["name"],
        media_types=[media_type],
        limit=25,
    )
    candidates = [
        item for item in result.get(key, []) if isinstance(item, dict)
    ]
    wanted = str(sonos_item["name"]).casefold().strip()
    exact = [
        item
        for item in candidates
        if str(item.get("name") or "").casefold().strip() == wanted
    ]
    if not exact:
        return None

    image = str(sonos_item.get("image") or "")
    if "mzstatic.com" in image:
        exact.sort(
            key=lambda item: not str(item.get("uri") or "").startswith("apple_music")
        )
    elif "scdn.co" in image:
        exact.sort(
            key=lambda item: not str(item.get("uri") or "").startswith("spotify")
        )

    mapped = _mapping(exact[0])
    mapped["sonos_favorite"] = True
    if sonos_item.get("image") and not mapped.get("image"):
        mapped["image"] = sonos_item["image"]
    return mapped


def _has_exact_apple_artist(result: dict[str, Any], query: str) -> bool:
    """Return whether search results contain the exact Apple Music artist."""
    wanted = query.casefold().strip()
    for artist in result.get("artists", []):
        if not isinstance(artist, dict):
            continue
        if (artist.get("name") or "").casefold().strip() != wanted:
            continue
        if str(artist.get("uri") or "").startswith("apple_music"):
            return True
    return False


async def _add_apple_exact_artist_fallback(
    mass, result: dict[str, Any], query: str, limit: int
) -> None:
    """Recover Apple artists missed by Apple's bare-name ranking.

    Apple Music can return related members instead of the band for a bare query
    (for example, "Eagles" returns Don Henley). A bounded "The <name>" artist
    search often exposes the exact provider-native artist identifier needed for
    artist -> album browsing. Only an exact-name match is promoted.
    """
    if _has_exact_apple_artist(result, query):
        return
    fallback = await mass.send_command(
        "music/search",
        search_query=f"The {query}",
        media_types=["artist"],
        limit=min(limit, 20),
        providers=["apple_music"],
    )
    wanted = query.casefold().strip()
    for artist in fallback.get("artists", []):
        if not isinstance(artist, dict):
            continue
        if (artist.get("name") or "").casefold().strip() != wanted:
            continue
        if not str(artist.get("uri") or "").startswith("apple_music"):
            continue
        result.setdefault("artists", []).insert(0, artist)
        return


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
    """Search selected Music Assistant providers without exposing an MA token."""
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    result = await mass.send_command(
        "music/search",
        search_query=msg["query"],
        media_types=["artist", "album", "track", "playlist", "radio"],
        limit=msg["limit"],
        providers=_provider_list(msg["provider"]),
    )
    if msg["provider"] in ("apple", "all"):
        await _add_apple_exact_artist_fallback(mass, result, msg["query"], msg["limit"])
    connection.send_result(msg["id"], _normalize_search(result))




@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/recents",
        vol.Required("config_entry_id"): str,
        vol.Required("queue_id"): str,
        vol.Optional("limit", default=40): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }
)
@websocket_api.async_response
async def ws_recents(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return recently played items for one Music Assistant queue.

    Music Assistant's playlog is user-scoped. Home Assistant playback can be
    attributed to a service/anonymous context, so a queue may have valid played
    items while the public playlog query returns no rows for the HA session.
    Merge the playlog with the already-played portion of the live queue so
    zone-specific Recents remains useful regardless of playback origin.
    """
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    result = await mass.send_command(
        "music/recently_played_items",
        limit=msg["limit"],
        queue_id=msg["queue_id"],
        fully_played_only=False,
    )

    recent_items = [_mapping(item) for item in result if isinstance(item, dict)]
    seen_uris = {item.get("uri") for item in recent_items if item.get("uri")}

    queue = await mass.send_command("player_queues/get", queue_id=msg["queue_id"])
    queue_items = await mass.send_command(
        "player_queues/items",
        queue_id=msg["queue_id"],
        limit=500,
        offset=0,
    )
    current_index = queue.get("current_index") if isinstance(queue, dict) else None
    if isinstance(queue_items, list):
        if isinstance(current_index, int):
            played_queue_items = queue_items[: current_index + 1]
        else:
            played_queue_items = queue_items
        for queue_item in reversed(played_queue_items):
            if not isinstance(queue_item, dict):
                continue
            media_item = queue_item.get("media_item")
            if not isinstance(media_item, dict):
                continue
            mapped = _mapping(media_item)
            uri = mapped.get("uri")
            if not uri or uri in seen_uris:
                continue
            recent_items.append(mapped)
            seen_uris.add(uri)
            if len(recent_items) >= msg["limit"]:
                break

    connection.send_result(msg["id"], recent_items[: msg["limit"]])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/favorites",
        vol.Required("config_entry_id"): str,
        vol.Optional("limit", default=40): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }
)
@websocket_api.async_response
async def ws_favorites(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return the union of Music Assistant favorites and household Sonos Favorites."""
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    commands = {
        "artists": "music/artists/library_items",
        "albums": "music/albums/library_items",
        "tracks": "music/tracks/library_items",
        "playlists": "music/playlists/library_items",
        "radio": "music/radios/library_items",
    }
    favorites: dict[str, list[dict[str, Any]]] = {}
    for key, command in commands.items():
        items = await mass.send_command(
            command,
            favorite=True,
            limit=msg["limit"],
            offset=0,
            order_by="sort_name",
            summary=False,
        )
        favorites[key] = [_mapping(item) for item in items if isinstance(item, dict)]

    sonos_favorites = await _native_sonos_favorites(hass)
    for sonos_item in sonos_favorites:
        media_type = str(sonos_item["media_type"])
        key = "radio" if media_type == "radio" else f"{media_type}s"
        existing = favorites.setdefault(key, [])
        resolved = await _resolve_sonos_favorite(mass, sonos_item, existing)
        if not resolved:
            continue
        resolved_uri = resolved.get("uri")
        if resolved_uri and any(item.get("uri") == resolved_uri for item in existing):
            continue
        existing.append(resolved)

    connection.send_result(msg["id"], favorites)


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
    connection.send_result(
        msg["id"], [_mapping(item) for item in result if isinstance(item, dict)]
    )


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
    connection.send_result(
        msg["id"], [_mapping(item) for item in result if isinstance(item, dict)]
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Family Music."""
    if not hass.data.setdefault(DOMAIN, {}).get("registered"):
        card_path = Path(__file__).parent / CARD_PATH
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
        )
        frontend.add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
        websocket_api.async_register_command(hass, ws_search)
        websocket_api.async_register_command(hass, ws_recents)
        websocket_api.async_register_command(hass, ws_favorites)
        websocket_api.async_register_command(hass, ws_artist_albums)
        websocket_api.async_register_command(hass, ws_album_tracks)
        hass.data[DOMAIN]["registered"] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Family Music."""
    return True
