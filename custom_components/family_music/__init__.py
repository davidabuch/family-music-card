"""Family Music integration setup and Music Assistant proxy commands."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.components.music_assistant.helpers import get_music_assistant_client
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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


def _browse_node(response: Any, entity_id: str) -> Any:
    """Extract one media browser node from a Home Assistant service response."""
    if not isinstance(response, dict):
        return None
    return response.get(entity_id)


def _browse_value(node: Any, key: str, default: Any = None) -> Any:
    """Read a field from either a BrowseMedia object or serialized dictionary."""
    if isinstance(node, dict):
        return node.get(key, default)
    return getattr(node, key, default)


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
        children = _browse_value(node, "children", [])
        if not isinstance(children, list):
            continue
        for child in children:
            if not _browse_value(child, "can_play", False):
                continue
            title = _browse_value(child, "title")
            content_id = _browse_value(child, "media_content_id")
            if not isinstance(title, str) or not isinstance(content_id, str):
                continue
            favorites.append(
                {
                    "name": title,
                    "media_type": media_type,
                    "image": _browse_value(child, "thumbnail"),
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
        vol.Required("type"): "family_music/group_members",
        vol.Required("config_entry_id"): str,
        vol.Required("player_entity_id"): str,
    }
)
@websocket_api.async_response
async def ws_group_members(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return Home Assistant media_player entities for one Music Assistant group."""
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    registry = er.async_get(hass)
    group_entry = registry.async_get(msg["player_entity_id"])
    group_player_id = (
        group_entry.unique_id
        if group_entry is not None
        and group_entry.domain == "media_player"
        and group_entry.platform == "music_assistant"
        else None
    )
    if not group_player_id:
        connection.send_result(msg["id"], [])
        return

    players = await mass.send_command("players/all")
    if not isinstance(players, list):
        connection.send_result(msg["id"], [])
        return

    group = next(
        (
            player
            for player in players
            if isinstance(player, dict) and player.get("player_id") == group_player_id
        ),
        None,
    )
    if not isinstance(group, dict):
        connection.send_result(msg["id"], [])
        return

    member_ids = group.get("group_members")
    if not isinstance(member_ids, list):
        connection.send_result(msg["id"], [])
        return

    entity_by_unique_id = {
        entry.unique_id: entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "media_player" and entry.platform == "music_assistant"
    }
    native_by_unique_id = {
        entry.unique_id: entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "media_player"
        and entry.platform == "sonos"
        and isinstance(entry.unique_id, str)
    }
    player_by_id = {
        player.get("player_id"): player
        for player in players
        if isinstance(player, dict) and isinstance(player.get("player_id"), str)
    }

    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    for player_id in member_ids:
        if not isinstance(player_id, str) or player_id == group_player_id or player_id in seen:
            continue
        seen.add(player_id)
        entity_id = entity_by_unique_id.get(player_id)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        player = player_by_id.get(player_id, {})
        friendly_name = (
            state.attributes.get("friendly_name")
            if state is not None
            else None
        ) or player.get("name") or entity_id
        members.append(
            {
                "entity_id": entity_id,
                "native_entity_id": native_by_unique_id.get(player_id),
                "player_id": player_id,
                "name": friendly_name,
                "available": state is not None and state.state != "unavailable",
            }
        )

    connection.send_result(msg["id"], members)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/player_peers",
        vol.Required("config_entry_id"): str,
        vol.Required("player_entity_ids"): [str],
    }
)
@websocket_api.async_response
async def ws_player_peers(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return native Sonos peers for Music Assistant media_player entities."""
    registry = er.async_get(hass)
    native_by_unique_id = {
        entry.unique_id: entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "media_player"
        and entry.platform == "sonos"
        and isinstance(entry.unique_id, str)
    }

    peers: dict[str, str] = {}
    for entity_id in msg["player_entity_ids"]:
        entry = registry.async_get(entity_id)
        if (
            entry is None
            or entry.domain != "media_player"
            or entry.platform != "music_assistant"
            or not isinstance(entry.unique_id, str)
        ):
            continue
        native_entity_id = native_by_unique_id.get(entry.unique_id)
        if native_entity_id:
            peers[entity_id] = native_entity_id

    connection.send_result(msg["id"], peers)


def _ma_sonos_group_context(
    players: list[dict[str, Any]], player_id: str
) -> dict[str, Any]:
    """Resolve one physical Sonos player's current native grouping context."""
    by_id = {
        str(player.get("player_id")): player
        for player in players
        if isinstance(player, dict) and isinstance(player.get("player_id"), str)
    }
    selected = by_id.get(player_id)
    if not selected:
        raise ValueError("Selected player not found")
    if selected.get("provider") != "sonos" or selected.get("type") == "group":
        raise ValueError("Dynamic grouping is available only for physical Sonos speakers")
    leader_id = str(selected.get("synced_to") or player_id)
    leader = by_id.get(leader_id)
    if not leader or leader.get("provider") != "sonos":
        raise ValueError("Sonos group coordinator is unavailable")
    member_ids = {leader_id}
    for key in ("group_members", "group_childs"):
        values = leader.get(key)
        if isinstance(values, list):
            member_ids.update(str(value) for value in values if isinstance(value, str))
    ordered_member_ids = [leader_id]
    for key in ("group_members", "group_childs"):
        values = leader.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value not in ordered_member_ids:
                    ordered_member_ids.append(value)
    return {
        "selected": selected,
        "leader": leader,
        "leader_id": leader_id,
        "member_ids": member_ids,
        "ordered_member_ids": ordered_member_ids,
        "by_id": by_id,
        "can_group_with": {
            str(value)
            for value in (leader.get("can_group_with") or [])
            if isinstance(value, str)
        },
    }


def _sonos_ip(player: dict[str, Any]) -> str | None:
    device_info = player.get("device_info")
    if not isinstance(device_info, dict):
        return None
    value = device_info.get("ip_address")
    return value if isinstance(value, str) and value else None


async def _delegate_sonos_coordinator(
    hass: HomeAssistant, leader: dict[str, Any], successor_id: str
) -> None:
    """Delegate coordinator role natively on Sonos without restarting playback."""
    ip_address = _sonos_ip(leader)
    if not ip_address:
        raise RuntimeError("Sonos coordinator IP address is unavailable")
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:DelegateGroupCoordinationTo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">
      <InstanceID>0</InstanceID>
      <NewCoordinator>{successor_id}</NewCoordinator>
      <RejoinGroup>0</RejoinGroup>
    </u:DelegateGroupCoordinationTo>
  </s:Body>
</s:Envelope>"""
    session = async_get_clientsession(hass)
    async with session.post(
        f"http://{ip_address}:1400/MediaRenderer/AVTransport/Control",
        data=body.encode(),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": '"urn:schemas-upnp-org:service:AVTransport:1#DelegateGroupCoordinationTo"',
        },
        timeout=4,
    ) as response:
        if response.status >= 400:
            raise RuntimeError(f"Sonos delegate failed with HTTP {response.status}")
        await response.read()


async def _wait_for_ma_sonos_successor(
    mass, successor_id: str, old_leader_id: str, attempts: int = 12
) -> list[dict[str, Any]]:
    """Wait for MA to observe native Sonos coordinator handoff."""
    latest: list[dict[str, Any]] = []
    for _ in range(attempts):
        raw = await mass.send_command("players/all")
        latest = (
            [player for player in raw if isinstance(player, dict)]
            if isinstance(raw, list)
            else []
        )
        try:
            context = _ma_sonos_group_context(latest, successor_id)
        except ValueError:
            context = None
        if (
            context
            and context["leader_id"] == successor_id
            and old_leader_id not in context["member_ids"]
        ):
            return latest
        await asyncio.sleep(0.15)
    raise RuntimeError("Native Sonos coordinator handoff was not confirmed")


async def _transfer_ma_queue_without_resume(
    mass, source_id: str, target_id: str
) -> None:
    """Move MA queue bookkeeping after Sonos has already handed off live playback."""
    raw = await mass.send_command("player_queues/all")
    queues = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    source = next((q for q in queues if q.get("queue_id") == source_id), None)
    if not source:
        return
    active = bool(source.get("active")) or source.get("state") not in (None, "idle")
    if not active:
        return
    await mass.send_command(
        "player_queues/transfer",
        source_queue_id=source_id,
        target_queue_id=target_id,
        auto_play=False,
    )


def _ma_player_id_for_entity(registry, entity_id: str) -> str | None:
    entry = registry.async_get(entity_id)
    if (
        entry is None
        or entry.domain != "media_player"
        or entry.platform != "music_assistant"
        or not isinstance(entry.unique_id, str)
    ):
        return None
    return entry.unique_id


@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/group_topology",
        vol.Required("config_entry_id"): str,
        vol.Required("player_entity_id"): str,
    }
)
@websocket_api.async_response
async def ws_group_topology(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return authoritative native Sonos membership for a physical MA player."""
    registry = er.async_get(hass)
    player_id = _ma_player_id_for_entity(registry, msg["player_entity_id"])
    if not player_id:
        connection.send_result(msg["id"], {"available": False, "speakers": []})
        return
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    raw = await mass.send_command("players/all")
    players = (
        [player for player in raw if isinstance(player, dict)]
        if isinstance(raw, list)
        else []
    )
    try:
        context = _ma_sonos_group_context(players, player_id)
    except ValueError:
        connection.send_result(msg["id"], {"available": False, "speakers": []})
        return

    ma_entity_by_unique_id = {
        entry.unique_id: entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "media_player"
        and entry.platform == "music_assistant"
        and isinstance(entry.unique_id, str)
    }
    native_entity_by_unique_id = {
        entry.unique_id: entry.entity_id
        for entry in registry.entities.values()
        if entry.domain == "media_player"
        and entry.platform == "sonos"
        and isinstance(entry.unique_id, str)
    }
    speakers = []
    for player in players:
        candidate_id = player.get("player_id")
        if (
            not isinstance(candidate_id, str)
            or player.get("provider") != "sonos"
            or player.get("type") == "group"
        ):
            continue
        entity_id = ma_entity_by_unique_id.get(candidate_id)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        speakers.append(
            {
                "player_id": candidate_id,
                "entity_id": entity_id,
                "native_entity_id": native_entity_by_unique_id.get(candidate_id),
                "name": (
                    state.attributes.get("friendly_name")
                    if state is not None
                    else None
                )
                or player.get("name")
                or entity_id,
                "available": player.get("available") is not False,
                "member": candidate_id in context["member_ids"],
                "coordinator": candidate_id == context["leader_id"],
            }
        )
    connection.send_result(
        msg["id"],
        {
            "available": True,
            "leader_id": context["leader_id"],
            "speakers": speakers,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "family_music/group_member",
        vol.Required("config_entry_id"): str,
        vol.Required("player_entity_id"): str,
        vol.Required("member_player_id"): str,
        vol.Required("action"): vol.In(("join", "leave")),
    }
)
@websocket_api.async_response
async def ws_group_member(hass: HomeAssistant, connection, msg: dict) -> None:
    """Join or leave one physical Sonos speaker through Music Assistant."""
    registry = er.async_get(hass)
    player_id = _ma_player_id_for_entity(registry, msg["player_entity_id"])
    if not player_id:
        connection.send_error(msg["id"], "invalid_player", "Selected player is not Music Assistant")
        return
    mass = get_music_assistant_client(hass, msg["config_entry_id"])
    raw = await mass.send_command("players/all")
    players = (
        [player for player in raw if isinstance(player, dict)]
        if isinstance(raw, list)
        else []
    )
    try:
        context = _ma_sonos_group_context(players, player_id)
        member_id = msg["member_player_id"]
        member = context["by_id"].get(member_id)
        if not member or member.get("provider") != "sonos" or member.get("type") == "group":
            raise ValueError("Group member must be a physical Sonos speaker")
        if member.get("available") is False:
            raise ValueError("Group member is unavailable")
        if msg["action"] == "join":
            if member_id not in context["member_ids"]:
                if member_id not in context["can_group_with"]:
                    raise ValueError("This speaker cannot join the selected Sonos group")
                if member.get("synced_to") and member.get("synced_to") != context["leader_id"]:
                    raise ValueError("Speaker is already participating in another Sonos group")
                child_members = member.get("group_members") or member.get("group_childs") or []
                if child_members:
                    raise ValueError("Speaker is already coordinating another Sonos group")
                await mass.send_command(
                    "players/cmd/group_many",
                    target_player=context["leader_id"],
                    child_player_ids=[member_id],
                )
        selected_player_id = None
        handoff = None
        if msg["action"] == "leave" and member_id in context["member_ids"]:
            remaining_ids = [
                item for item in context["member_ids"] if item != member_id
            ]
            if not remaining_ids:
                raise ValueError("At least one Sonos speaker must remain in the session")
            if member_id != context["leader_id"]:
                await mass.send_command("players/cmd/ungroup", player_id=member_id)
            else:
                ordered_remaining = [
                    item
                    for item in context["ordered_member_ids"]
                    if item != context["leader_id"] and item in context["member_ids"]
                ]
                selected_player_id = (
                    ordered_remaining[0] if ordered_remaining else remaining_ids[0]
                )
                try:
                    await _delegate_sonos_coordinator(
                        hass, context["leader"], selected_player_id
                    )
                except (RuntimeError, TimeoutError, OSError):
                    await mass.send_command("players/cmd/ungroup", player_id=member_id)
                    handoff = "music_assistant_fallback"
                    raw_after = await mass.send_command("players/all")
                    players_after = (
                        [player for player in raw_after if isinstance(player, dict)]
                        if isinstance(raw_after, list)
                        else []
                    )
                    for candidate_id in remaining_ids:
                        try:
                            candidate_context = _ma_sonos_group_context(
                                players_after, candidate_id
                            )
                        except ValueError:
                            continue
                        if candidate_context["leader_id"] in remaining_ids:
                            selected_player_id = candidate_context["leader_id"]
                            break
                else:
                    await _wait_for_ma_sonos_successor(
                        mass, selected_player_id, context["leader_id"]
                    )
                    await _transfer_ma_queue_without_resume(
                        mass, context["leader_id"], selected_player_id
                    )
                    handoff = "native_sonos_delegate"
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_group_change", str(err))
        return

    response = {"accepted": True}
    if handoff:
        response["handoff"] = handoff
    if selected_player_id:
        response["selected_player_id"] = selected_player_id
        response["selected_player_entity_id"] = next(
            (
                entry.entity_id
                for entry in registry.entities.values()
                if entry.domain == "media_player"
                and entry.platform == "music_assistant"
                and entry.unique_id == selected_player_id
            ),
            None,
        )
    connection.send_result(msg["id"], response)


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


async def _async_register_card_resource(hass: HomeAssistant) -> None:
    """Ensure the Family Music card is a versioned Lovelace module resource."""
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return

    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        return

    if isinstance(resources, ResourceStorageCollection):
        await resources.async_get_info()

    resource_url = f"{CARD_URL}?v={VERSION}"
    for item in resources.async_items():
        existing_url = str(item.get("url") or "")
        if existing_url.split("?", 1)[0] != CARD_URL:
            continue
        if existing_url == resource_url:
            return
        await resources.async_update_item(
            item["id"],
            {"res_type": "module", "url": resource_url},
        )
        return

    await resources.async_create_item(
        {"res_type": "module", "url": resource_url}
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Family Music."""
    if not hass.data.setdefault(DOMAIN, {}).get("registered"):
        card_path = Path(__file__).parent / CARD_PATH
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
        )
        await _async_register_card_resource(hass)
        websocket_api.async_register_command(hass, ws_search)
        websocket_api.async_register_command(hass, ws_group_members)
        websocket_api.async_register_command(hass, ws_player_peers)
        websocket_api.async_register_command(hass, ws_group_topology)
        websocket_api.async_register_command(hass, ws_group_member)
        websocket_api.async_register_command(hass, ws_recents)
        websocket_api.async_register_command(hass, ws_favorites)
        websocket_api.async_register_command(hass, ws_artist_albums)
        websocket_api.async_register_command(hass, ws_album_tracks)
        hass.data[DOMAIN]["registered"] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Family Music."""
    return True
