"""Standalone Family Music local web/PWA server.

This service talks directly to Music Assistant's authenticated HTTP API.
It does not require Home Assistant credentials or a Home Assistant session.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

try:
    from standalone.temporary_groups import Observation, TemporaryGroups
except ModuleNotFoundError:  # Direct execution inside the standalone App image.
    from temporary_groups import Observation, TemporaryGroups

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"

MA_URL = os.environ.get("MA_URL", "http://127.0.0.1:8095").rstrip("/")
MA_TOKEN = os.environ.get("MA_TOKEN", "")
PORT = int(os.environ.get("PORT", "8099"))
MAX_JSON_BODY_BYTES = 64 * 1024
MAX_MA_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 15
MAX_CONCURRENT_REQUESTS = 6
API_REQUEST_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
ARTWORK_CACHE_TTL = 3600
ARTWORK_CACHE_MAX_BYTES = 24 * 1024 * 1024
ARTWORK_CACHE: dict[str, tuple[float, str, bytes]] = {}
ARTWORK_CACHE_LOCK = threading.Lock()


PLAYER_ALLOWLIST = {
    item.strip()
    for item in os.environ.get("FAMILY_MUSIC_PLAYER_IDS", "").split(",")
    if item.strip()
}


NATIVE_SONOS_SESSIONS: set[str] = set()
GROUP_LOCK = threading.RLock()
TEMPORARY_GROUPS = TemporaryGroups()
APP_VERSION = "0.2.58"
REQUEST_CONTEXT = threading.local()
_LOGGER = logging.getLogger("family_music.standalone")


def diagnostic(phase: str, **detail: Any) -> None:
    """Bounded structured origin evidence; never include URLs, tokens or media."""
    detail.setdefault("request_id", getattr(REQUEST_CONTEXT, "request_id", ""))
    allowed = {key: str(value).replace("\n", " ")[:180] for key, value in detail.items()
               if key in {"request_id", "selected", "target", "action", "error_type", "members"}}
    try:
        _LOGGER.warning("Family Music %s %s", phase, json.dumps(allowed, separators=(",", ":")))
    except Exception:
        # Response correlation headers and the bounded browser trace remain available.
        pass


class MAError(RuntimeError):
    """Raised when Music Assistant rejects a command."""


class AuthorizationError(ValueError):
    """Raised when a caller targets a destination outside Family Music scope."""


class MAClient:
    """Minimal synchronous Music Assistant HTTP API client."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def command(self, command: str, **args: Any) -> Any:
        if not self.token:
            raise MAError("Music Assistant token is not configured")
        payload = json.dumps(
            {
                "message_id": secrets.token_hex(8),
                "command": command,
                "args": args,
            }
        ).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = response.read(MAX_MA_RESPONSE_BYTES + 1)
                if len(raw) > MAX_MA_RESPONSE_BYTES:
                    raise MAError("Music Assistant response exceeded safety limit")
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")
            raise MAError(f"Music Assistant HTTP {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            raise MAError(f"Unable to reach Music Assistant: {err.reason}") from err

        data = json.loads(raw or b"{}")
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            if isinstance(error, dict):
                message = error.get("message") or error.get("details") or str(error)
            else:
                message = str(error)
            raise MAError(message)
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data


client = MAClient(MA_URL, MA_TOKEN)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _item_image(item: dict[str, Any]) -> str | dict[str, str] | None:
    image = item.get("image")
    if isinstance(image, str):
        return image
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        images = metadata.get("images")
        if isinstance(images, list):
            for entry in images:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    # Keep MA's proxy identifier: the browser can fetch artwork
                    # through our authenticated MA proxy instead of relying on
                    # the original provider CDN being reachable on the device.
                    proxy_id = entry.get("proxy_id")
                    if isinstance(proxy_id, str) and len(proxy_id) == 64 and all(
                        character in "0123456789abcdefABCDEF" for character in proxy_id
                    ):
                        return {"path": entry["path"], "proxy_id": proxy_id}
                    return entry["path"]
    return None


def normalize_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    result = {
        "uri": item.get("uri"),
        "name": item.get("name") or item.get("title"),
        "media_type": item.get("media_type") or item.get("type"),
        "image": _item_image(item),
        "favorite": bool(item.get("favorite", False)),
        "duration": item.get("duration"),
    }
    mappings = item.get("provider_mappings") or []
    providers = sorted({
        str(mapping.get("provider_instance") or mapping.get("provider_domain") or "")
        .split("--")[0]
        for mapping in mappings
        if isinstance(mapping, dict)
    })
    if providers:
        result["providers"] = providers
    instances = sorted({str(m.get("provider_instance")) for m in mappings
                        if isinstance(m, dict) and m.get("provider_instance")})
    if instances:
        result["provider_instances"] = instances
    artists = item.get("artists")
    if isinstance(artists, list):
        result["artists"] = [
            {"name": artist.get("name"), "uri": artist.get("uri")}
            for artist in artists
            if isinstance(artist, dict)
        ]
    album = item.get("album")
    if isinstance(album, dict):
        result["album"] = {"name": album.get("name"), "uri": album.get("uri")}
    return {key: value for key, value in result.items() if value is not None}


def get_players() -> list[dict[str, Any]]:
    raw = client.command("players/all")
    players = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if PLAYER_ALLOWLIST:
        players = [item for item in players if item.get("player_id") in PLAYER_ALLOWLIST]
    players.sort(key=lambda item: str(item.get("name") or item.get("display_name") or ""))
    return players


def _allowed_destination_ids(players: list[dict[str, Any]] | None = None) -> set[str] | None:
    """Return all queue/player IDs Family Music is permitted to address."""
    if not PLAYER_ALLOWLIST:
        return None
    scoped_players = get_players() if players is None else players
    allowed = set(PLAYER_ALLOWLIST)
    for player in scoped_players:
        for key in ("player_id", "active_source", "synced_to"):
            value = player.get(key)
            if isinstance(value, str) and value:
                allowed.add(value)
    return allowed


def _require_allowed_destination(
    destination_id: str, players: list[dict[str, Any]] | None = None
) -> None:
    allowed = _allowed_destination_ids(players)
    if allowed is not None and destination_id not in allowed:
        raise AuthorizationError("Destination is outside the configured Family Music scope")


def get_queues(players: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    raw = client.command("player_queues/all")
    queues = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    allowed = _allowed_destination_ids(players)
    if allowed is not None:
        queues = [queue for queue in queues if queue.get("queue_id") in allowed]
    return queues


def _sonos_group_context(
    player_id: str, players: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Resolve authoritative native-Sonos membership for one physical player."""
    players = get_players() if players is None else players
    by_id = {
        str(player.get("player_id")): player
        for player in players
        if isinstance(player.get("player_id"), str)
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

    can_group_with = {
        str(value)
        for value in (leader.get("can_group_with") or [])
        if isinstance(value, str)
    }
    physical = [
        player
        for player in players
        if player.get("provider") == "sonos"
        and player.get("type") != "group"
        and isinstance(player.get("player_id"), str)
    ]
    return {
        "leader_id": leader_id,
        "leader": leader,
        "selected": selected,
        "member_ids": member_ids,
        "can_group_with": can_group_with,
        "players": physical,
        "by_id": by_id,
    }


def change_sonos_group(player_id: str, member_id: str, action: str) -> dict[str, Any]:
    with GROUP_LOCK:
        return _change_sonos_group(player_id, member_id, action)


def _change_sonos_group(player_id: str, member_id: str, action: str) -> dict[str, Any]:
    """Join/leave a physical Sonos member using Music Assistant native grouping."""
    context = _sonos_group_context(player_id)
    leader_id = context["leader_id"]
    member_ids = context["member_ids"]
    member = context["by_id"].get(member_id)
    if not member or member.get("provider") != "sonos" or member.get("type") == "group":
        raise ValueError("Group member must be a physical Sonos speaker")
    if member.get("available") is False:
        raise ValueError("Group member is unavailable")

    if action == "join":
        if member_id in member_ids:
            return {"accepted": True, "already_member": True, "leader_id": leader_id}
        if member_id not in context["can_group_with"]:
            raise ValueError("This speaker cannot join the selected Sonos group")
        if member.get("synced_to") and member.get("synced_to") != leader_id:
            raise ValueError("Speaker is already participating in another Sonos group")
        child_members = member.get("group_members") or member.get("group_childs") or []
        if child_members:
            raise ValueError("Speaker is already coordinating another Sonos group")
        before = _ownership_observation(member_ids | {member_id})
        diagnostic("group_service_start", selected=player_id, target=member_id, action=action)
        result = client.command(
            "players/cmd/group_many",
            target_player=leader_id,
            child_player_ids=[member_id],
        )
        diagnostic("group_service_accepted", selected=player_id, target=member_id, action=action)
        if before is not None:
            TEMPORARY_GROUPS.changed(before, player_id, member_id, "join")
        return {"accepted": True, "outcome": "accepted", "leader_id": leader_id,
                "confirmation_timeout_ms": 30000, "result": result}

    if action == "leave":
        if member_id not in member_ids:
            return {"accepted": True, "already_removed": True, "leader_id": leader_id}
        remaining_ids = [item for item in member_ids if item != member_id]
        if not remaining_ids:
            raise ValueError("At least one Sonos speaker must remain in the session")
        # Explicit manual changes relinquish the old cleanup lease. Never guess
        # that a surviving subset is still ours after a coordinator handoff.
        TEMPORARY_GROUPS.forget(member_ids)
        diagnostic("group_service_start", selected=player_id, target=member_id, action=action)
        result = client.command("players/cmd/ungroup", player_id=member_id)
        diagnostic("group_service_accepted", selected=player_id, target=member_id, action=action)
        if member_id != leader_id:
            return {"accepted": True, "leader_id": leader_id, "result": result}

        # Music Assistant transfers sync leadership when an active leader is
        # ungrouped. Re-observe authoritative topology so the UI follows the
        # surviving session instead of staying attached to the removed leader.
        refreshed = get_players()
        successor_id = None
        for candidate_id in sorted(remaining_ids):
            try:
                candidate_context = _sonos_group_context(candidate_id, refreshed)
            except ValueError:
                continue
            if (candidate_context["leader_id"] in remaining_ids
                    and member_id not in candidate_context["member_ids"]):
                successor_id = candidate_context["leader_id"]
                break
        return {
            "accepted": True,
            "leader_id": successor_id,
            "selected_player_id": successor_id,
            "result": result,
        }

    raise ValueError("Invalid group action")



def _cleanup_observation(member_ids: frozenset[str] | set[str]) -> Observation:
    """Read fresh native topology/transport, requiring MA agreement and scope.

    Use configured MA device addresses, not arbitrary discovery results. Native
    SOAP queries have explicit timeouts and bypass SoCo's topology cache.
    """
    from soco import SoCo

    raw = client.command("players/all")
    if not isinstance(raw, list):
        raise ValueError("Music Assistant topology unavailable")
    players = [item for item in raw if isinstance(item, dict)]
    by_id = {player.get("player_id"): player for player in players}
    permanent_ids = {player.get("player_id") for player in players if player.get("type") == "group"}
    groups, leaders, states = {}, {}, {}
    protected = set()
    for member_id in member_ids:
        _require_allowed_destination(member_id, players)
        player = by_id.get(member_id, {})
        if (player.get("provider") != "sonos" or player.get("type") == "group"
                or player.get("available") is not True):
            raise ValueError("Cannot verify physical speaker availability")
        source = player.get("active_source")
        if source in permanent_ids or player.get("synced_to") in permanent_ids:
            protected.add(member_id)
        address = (player.get("device_info") or {}).get("ip_address")
        if not address:
            raise ValueError("Physical speaker address unavailable")
        speaker = SoCo(address)
        payload = speaker.zoneGroupTopology.GetZoneGroupState([], timeout=2)["ZoneGroupState"]
        if len(payload) > 1024 * 1024:
            raise ValueError("Sonos topology exceeds safety limit")
        topology = ElementTree.fromstring(payload)
        matches = []
        for group in topology.iter("ZoneGroup"):
            # Include satellites too: a bonded pair must not be mistaken for a solo.
            ids = frozenset(node.attrib["UUID"] for node in group.iter()
                            if node.tag in {"ZoneGroupMember", "Satellite"})
            if member_id in ids:
                matches.append((ids, group.attrib.get("Coordinator")))
        if len(matches) != 1:
            raise ValueError("Native Sonos group is ambiguous")
        groups[member_id], leaders[member_id] = matches[0]
        ma_context = _sonos_group_context(member_id, players)
        if (ma_context["member_ids"] != set(groups[member_id])
                or ma_context["leader_id"] != leaders[member_id]):
            raise ValueError("Native and Music Assistant topology disagree")
        transport = speaker.avTransport.GetTransportInfo([("InstanceID", 0)], timeout=2)
        native_state = {
            "PAUSED_PLAYBACK": "paused", "STOPPED": "idle", "NO_MEDIA_PRESENT": "off",
        }.get(transport.get("CurrentTransportState"), "unknown")
        states[member_id] = native_state if (
            transport.get("CurrentTransportStatus") == "OK"
            and player.get("state") in {"paused", "idle", "off"}
        ) else "unknown"
    return Observation(groups, leaders, states, protected)


def _ownership_observation(member_ids: set[str]) -> Observation | None:
    try:
        return _cleanup_observation(member_ids)
    except Exception:
        # Failure to earn an automatic cleanup lease must not break manual grouping.
        TEMPORARY_GROUPS.forget(member_ids)
        diagnostic("cleanup_ownership_not_acquired", members=sorted(member_ids))
        return None


def _idle_cleanup_tick(stop: threading.Event | None = None) -> None:
    with GROUP_LOCK:
        TEMPORARY_GROUPS.tick(
            _cleanup_observation,
            lambda member: client.command("players/cmd/ungroup", player_id=member),
            lambda phase, members: diagnostic(phase, members=members),
            stop.is_set if stop is not None else lambda: False,
        )


def _idle_cleanup_worker(stop: threading.Event) -> None:
    # One backend task. No browser dependency, no network traffic without leases.
    while not stop.wait(30):
        _idle_cleanup_tick(stop)


def _name_tokens(value: str) -> set[str]:
    import re

    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _matching_item(wanted: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    exact = [
        item for item in candidates
        if str(item.get("name") or "").casefold().strip() == wanted.casefold().strip()
    ]
    if exact:
        return exact[0]
    tokens = _name_tokens(wanted)
    return next(
        (item for item in candidates
         if tokens and tokens <= _name_tokens(str(item.get("name") or ""))),
        None,
    )


def _sonos_household():
    from soco.discovery import discover

    speakers = discover(timeout=3) or set()
    if not speakers:
        return None
    return sorted(speakers, key=lambda item: item.ip_address)[0]


def native_sonos_favorites() -> list[dict[str, Any]]:
    """Expose all native Sonos favorites, including items absent from MA."""
    try:
        speaker = _sonos_household()
        if speaker is None:
            return []
        entries = speaker.music_library.get_sonos_favorites() or []
    except Exception as err:
        print("[family-music] Sonos favorites unavailable: " + str(err), flush=True)
        return []
    result = []
    for index, entry in enumerate(entries):
        title = getattr(entry, "title", None)
        if not title:
            continue
        item_class = str(getattr(entry, "item_class", "") or "").lower()
        kind = (
            "playlist" if "playlist" in item_class else
            "album" if "album" in item_class else
            "radio" if "broadcast" in item_class or "radio" in item_class else
            "track"
        )
        result.append({
            "name": str(title),
            "media_type": kind,
            "image": getattr(entry, "album_art_uri", None),
            "uri": "sonos-favorite://" + str(index),
            "sonos_favorite": True,
        })
    return result


def resolve_group_sonos_favorite(entry: Any) -> str:
    """Find an unambiguous MA catalog equivalent for a Sonos favorite.

    MA virtual groups cannot be addressed through Sonos UPnP. Never redirect
    playback to an arbitrary physical speaker or guess among catalog matches.
    """
    title = str(getattr(entry, "title", "") or "").strip()
    item_class = str(getattr(entry, "item_class", "") or "").lower()
    kind = (
        "playlist" if "playlist" in item_class else
        "album" if "album" in item_class else
        "radio" if "broadcast" in item_class or "radio" in item_class else
        "track"
    )
    key = "radio" if kind == "radio" else kind + "s"
    raw = client.command(
        "music/search", search_query=title, media_types=[kind],
        limit=25, providers=["apple_music", "spotify"],
    )
    candidates = raw.get(key, []) if isinstance(raw, dict) else []
    matches = {
        str(item.get("uri")) for item in candidates
        if isinstance(item, dict)
        and str(item.get("name") or item.get("title") or "").casefold().strip()
        == title.casefold()
        and item.get("uri")
    }
    if len(matches) != 1:
        raise ValueError(
            "This Sonos favorite cannot be resolved uniquely for a speaker group. "
            "Use its Apple Music or Spotify catalog version instead."
        )
    return next(iter(matches))


def play_native_sonos_favorite(queue_id: str, uri: str) -> None:
    """Play a Sonos favorite on its matching physical Sonos speaker."""
    from soco.discovery import discover

    try:
        index = int(uri.removeprefix("sonos-favorite://"))
    except ValueError as err:
        raise ValueError("Invalid Sonos Favorite") from err
    if index < 0:
        raise ValueError("Invalid Sonos Favorite")
    queues = get_queues()
    queue = next((q for q in queues if q.get("queue_id") == queue_id), None)
    if queue is None:
        raise ValueError("Selected player queue not found")
    player_id = str(queue.get("queue_id") or "")
    players = get_players()
    player = next((p for p in players if p.get("player_id") == player_id), None)
    if player is None:
        raise ValueError("Selected player not found")
    speakers = discover(timeout=3) or set()
    speaker = next((sp for sp in speakers if sp.uid == player_id), None)
    source = speaker or _sonos_household()
    if source is None:
        raise ValueError("Sonos household is not reachable")
    entries = source.music_library.get_sonos_favorites() or []
    if index >= len(entries):
        raise ValueError("Sonos Favorite is no longer available; refresh Favorites")
    entry = entries[index]
    if speaker is None:
        # Virtual MA group: play only an unambiguous equivalent through MA.
        media = resolve_group_sonos_favorite(entry)
        client.command("player_queues/play_media", queue_id=queue_id, media=media)
        NATIVE_SONOS_SESSIONS.discard(queue_id)
        return
    coordinator = getattr(getattr(speaker, "group", None), "coordinator", None)
    speaker = coordinator or speaker
    play_sonos_favorite(speaker, entry)
    NATIVE_SONOS_SESSIONS.add(queue_id)


def play_sonos_favorite(speaker: Any, favorite: Any) -> None:
    """Follow the established Sonos favorite playback contract.

    Radio streams use their referenced URI and embedded resource metadata.
    Albums/playlists/tracks must be enqueued, not passed to play_uri.
    """
    reference = favorite.reference
    uri = reference.get_uri()
    source = speaker.music_source_from_uri(uri)
    if source in {"RADIO", "LINE_IN"} or reference.item_class == "object.item.audioItem.audioBook":
        speaker.play_uri(
            uri, title=favorite.title, meta=favorite.resource_meta_data or ""
        )
    else:
        speaker.clear_queue()
        speaker.add_to_queue(reference)
        speaker.play_from_queue(0)


def _native_sonos_transport(queue_id: str, action: str) -> bool:
    """Use the physical player when MA is not actively managing its queue."""
    REQUEST_CONTEXT.phase = "topology"
    diagnostic("transport_topology_start", selected=queue_id, action=action)
    queues = get_queues()
    queue = next((q for q in queues if q.get("queue_id") == queue_id), None)
    players = get_players()
    player = next((p for p in players if p.get("player_id") == queue_id), None)
    if (action == "play" and player and player.get("state") == "idle"
            and not player.get("synced_to")):
        return False
    if (
        queue_id not in NATIVE_SONOS_SESSIONS
        and queue
        and queue.get("state") in {"playing", "paused"}
    ):
        return False
    from soco.discovery import discover

    speaker = next(
        (sp for sp in (discover(timeout=3) or set()) if sp.uid == queue_id),
        None,
    )
    if speaker is None:
        return False
    speaker = _verified_coordinator(speaker)
    diagnostic("transport_topology_resolved", selected=queue_id, target=speaker.uid)
    REQUEST_CONTEXT.phase = "native_service"
    diagnostic("native_service_start", selected=queue_id, target=speaker.uid, action=action)
    try:
        getattr(speaker, action)()
    except Exception as err:
        # UPnP 701 means this source cannot perform the requested transition.
        # Music Assistant may still have a navigable album/playlist queue.
        # Fall back only for unsupported navigation, never for play/pause errors.
        if action in {"next", "previous"} and getattr(err, "error_code", None) == 701:
            NATIVE_SONOS_SESSIONS.discard(queue_id)
            return False
        raise
    diagnostic("native_service_accepted", selected=queue_id, target=speaker.uid, action=action)
    return True


def _verified_coordinator(speaker: Any) -> Any:
    """Resolve only from observed native membership; never pick an arbitrary speaker."""
    group = speaker.group
    coordinator = getattr(group, "coordinator", None)
    members = {member.uid for member in getattr(group, "members", [])}
    if (coordinator is None or speaker.uid not in members or coordinator.uid not in members
            or not coordinator.is_coordinator):
        raise ValueError("Cannot verify current Sonos coordinator; refresh and try again")
    _require_allowed_destination(coordinator.uid)
    return coordinator


def previous_transport(queue_id: str) -> dict[str, Any]:
    """Restart the current track unless it is already near its beginning.

    Select the same playback authority for both seeking and navigation.
    A Sonos source without a previous item may reject Previous with 701;
    that is an end-of-queue condition, not a server failure.
    """
    queues = get_queues()
    queue = next((q for q in queues if q.get("queue_id") == queue_id), None)
    players = get_players()
    player = next((p for p in players if p.get("player_id") == queue_id), None)
    ma_active = bool(
        queue and queue.get("state") in {"playing", "paused"}
        and queue_id not in NATIVE_SONOS_SESSIONS
    )
    if ma_active:
        elapsed = float(queue.get("elapsed_time") or 0)
        if elapsed >= 3:
            result = client.command("player_queues/seek", queue_id=queue_id, position=0)
            return {"result": result, "restarted": True}
        result = client.command("player_queues/previous", queue_id=queue_id)
        return {"result": result, "restarted": False}

    from soco.discovery import discover

    speaker = next(
        (sp for sp in (discover(timeout=3) or set()) if sp.uid == queue_id),
        None,
    )
    if speaker is None:
        raise ValueError("Selected Sonos speaker is unavailable")
    speaker = _verified_coordinator(speaker)
    # MA's current_media.elapsed_time can remain zero throughout playback.
    # Prefer the physical Sonos position; MA player.elapsed_time is a fallback.
    track = speaker.get_current_track_info() or {}
    position = str(track.get("position") or "")
    parts = position.split(":")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        elapsed = sum(int(part) * factor for part, factor in zip(parts, (3600, 60, 1)))
    else:
        elapsed = float((player or {}).get("elapsed_time") or 0)
    if elapsed >= 3:
        speaker.seek("0:00:00")
        return {"sonos_direct": True, "restarted": True}
    try:
        speaker.previous()
    except Exception as err:
        if str(getattr(err, "error_code", None)) == "701":
            raise ValueError(
                "This Sonos source has no available previous track"
            ) from err
        raise
    return {"sonos_direct": True, "restarted": False}


def sonos_playback_modes(queue_id: str) -> dict[str, Any]:
    """Read native Sonos play mode without modifying playback."""
    from soco.discovery import discover

    speaker = next(
        (sp for sp in (discover(timeout=3) or set()) if sp.uid == queue_id),
        None,
    )
    if speaker is None:
        return {"available": False}
    coordinator = getattr(getattr(speaker, "group", None), "coordinator", None)
    speaker = coordinator or speaker
    mode = str(speaker.play_mode or "NORMAL").upper()
    try:
        actions = speaker.avTransport.GetCurrentTransportActions(
            [("InstanceID", 0)]
        ).get("Actions", "")
        available_actions = {action.strip().lower() for action in actions.split(",")}
    except Exception:
        available_actions = set()
    return {
        "available": True,
        "can_previous": (
            "previous" in available_actions or "seek" in available_actions
        ) if available_actions else None,
        "shuffle": mode.startswith("SHUFFLE"),
        "repeat": (
            "one" if mode.endswith("ONE") else
            "all" if "REPEAT" in mode else "off"
        ),
    }


def set_sonos_playback_mode(queue_id: str, action: str, value: Any) -> dict[str, Any]:
    from soco.discovery import discover

    speaker = next(
        (sp for sp in (discover(timeout=3) or set()) if sp.uid == queue_id),
        None,
    )
    if speaker is None:
        raise ValueError("Selected Sonos speaker is unavailable")
    coordinator = getattr(getattr(speaker, "group", None), "coordinator", None)
    speaker = coordinator or speaker
    current = str(speaker.play_mode or "NORMAL").upper()
    shuffle = current.startswith("SHUFFLE")
    repeat = "one" if current.endswith("ONE") else "all" if "REPEAT" in current else "off"
    if action == "shuffle" and isinstance(value, bool):
        shuffle = value
    elif action == "repeat" and value in {"off", "all", "one"}:
        repeat = value
    else:
        raise ValueError("Invalid playback mode")
    # SoCo uses Sonos' native AVTransport play modes.
    target = (
        "SHUFFLE_REPEAT_ONE" if shuffle and repeat == "one" else
        "SHUFFLE" if shuffle and repeat == "all" else
        "SHUFFLE_NOREPEAT" if shuffle else
        "REPEAT_ONE" if repeat == "one" else
        "REPEAT_ALL" if repeat == "all" else "NORMAL"
    )
    speaker.play_mode = target
    return {"sonos_direct": True, "mode": target}


def merge_sonos_favorites(result: dict[str, list[dict[str, Any]]],
                          sonos_items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Resolve Sonos titles to MA playable URIs; never expose unplayable entries."""
    for sonos_item in sonos_items:
        kind = sonos_item["media_type"]
        key = "radio" if kind == "radio" else f"{kind}s"
        existing = result.setdefault(key, [])
        match = _matching_item(sonos_item["name"], existing)
        if match is None:
            try:
                found = client.command(
                    "music/search", search_query=sonos_item["name"],
                    media_types=[kind], limit=25,
                )
                candidates = found.get(key, []) if isinstance(found, dict) else []
                match = _matching_item(sonos_item["name"], candidates)
            except MAError:
                continue
        if not match:
            continue
        resolved = normalize_item(match)
        if not resolved.get("uri"):
            continue
        if any(item.get("uri") == resolved["uri"] for item in existing):
            continue
        resolved["sonos_favorite"] = True
        if not resolved.get("image") and sonos_item.get("image"):
            resolved["image"] = sonos_item["image"]
        existing.append(resolved)
    return result


def _visible_playlist(item: dict[str, Any]) -> bool:
    """Exclude only explicitly configured source instances; fail open for unknown mappings."""
    configured = os.environ.get("FAMILY_MUSIC_EXCLUDED_PLAYLIST_INSTANCES", "")
    excluded = {value.strip() for value in configured.split(",") if value.strip()}
    instances = set(item.get("provider_instances", []))
    return not (excluded and instances and instances.issubset(excluded))


def favorites(limit: int = 40) -> dict[str, list[dict[str, Any]]]:
    commands = {
        "artists": "music/artists/library_items",
        "albums": "music/albums/library_items",
        "tracks": "music/tracks/library_items",
        "playlists": "music/playlists/library_items",
        "radio": "music/radios/library_items",
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for key, command in commands.items():
        raw = client.command(
            command,
            favorite=True,
            limit=limit,
            offset=0,
            order_by="sort_name",
            summary=False,
        )
        result[key] = [normalized for item in raw if isinstance(item, dict)
                       for normalized in [normalize_item(item)]
                       if key != "playlists" or _visible_playlist(normalized)]
    # MA may import saved Apple Music/Spotify playlists without marking them
    # as MA favorites. Show them as saved library items without changing flags.
    saved = client.command(
        "music/playlists/library_items",
        limit=500,
        offset=0,
        order_by="sort_name",
        summary=False,
    )
    known = {item.get("uri") for item in result["playlists"]}
    for item in saved if isinstance(saved, list) else []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_item(item)
        if (_visible_playlist(normalized) and normalized.get("uri") not in known
                and set(normalized.get("providers", [])) & {"apple_music", "spotify"}):
            normalized["saved_library"] = True
            result["playlists"].append(normalized)
            known.add(normalized.get("uri"))
    result["sonos_favorites"] = native_sonos_favorites()
    return result




FAVORITE_ACCOUNT_INSTANCES = {
    "david": ("spotify--Q5Eakxzw", "apple_music--qpuX24cG"),
    "kaelah": ("spotify--ooVGVwHn",),
    "alexis": ("apple_music--ydAVUe7w",),
}


def account_favorites(profile: str, limit: int = 40) -> dict[str, list[dict[str, Any]]]:
    """Fetch actual provider-scoped favorites, not inferred cross-account mappings."""
    if profile not in FAVORITE_ACCOUNT_INSTANCES:
        raise ValueError("Unknown favorites profile")
    commands = {
        "artists": "music/artists/library_items",
        "albums": "music/albums/library_items",
        "tracks": "music/tracks/library_items",
        "playlists": "music/playlists/library_items",
        "radio": "music/radios/library_items",
    }
    result: dict[str, list[dict[str, Any]]] = {key: [] for key in commands}
    for instance in FAVORITE_ACCOUNT_INSTANCES[profile]:
        source = "apple" if instance.startswith("apple_music--") else "spotify"
        for kind, command in commands.items():
            raw = client.command(
                command, provider=instance, favorite=True, limit=limit,
                offset=0, order_by="sort_name", summary=False,
            )
            for item in raw if isinstance(raw, list) else []:
                if not isinstance(item, dict):
                    continue
                normalized = normalize_item(item)
                if kind == "playlists" and not _visible_playlist(normalized):
                    continue
                normalized["favorite_source"] = source
                if not any(existing.get("uri") == normalized.get("uri")
                           and existing.get("favorite_source") == source
                           for existing in result[kind]):
                    result[kind].append(normalized)
        # Include imported/saved playlists, scoped to the account instance.
        saved = client.command(
            "music/playlists/library_items", provider=instance, limit=500,
            offset=0, order_by="sort_name", summary=False,
        )
        for item in saved if isinstance(saved, list) else []:
            if not isinstance(item, dict):
                continue
            normalized = normalize_item(item)
            if not _visible_playlist(normalized):
                continue
            normalized["favorite_source"] = source
            if not any(existing.get("uri") == normalized.get("uri")
                       and existing.get("favorite_source") == source
                       for existing in result["playlists"]):
                normalized["saved_library"] = True
                result["playlists"].append(normalized)
    result["sonos_favorites"] = native_sonos_favorites() if profile == "david" else []
    return result


def favorite_ownership_diagnostic() -> dict[str, Any]:
    """Read-only provider-scoped MA probe; never infer ownership from mappings."""
    instances = {
        "david": "apple_music--qpuX24cG",
        "alexis": "apple_music--ydAVUe7w",
    }
    result: dict[str, Any] = {
        "note": "Provider association does not prove favorite ownership.",
        "accounts": {},
    }
    for name, instance in instances.items():
        account: dict[str, Any] = {}
        for kind in ("playlists", "tracks", "albums"):
            try:
                raw = client.command(
                    f"music/{kind}/library_items",
                    provider=instance,
                    favorite=True,
                    limit=15,
                    offset=0,
                    order_by="sort_name",
                    summary=False,
                )
                items = [normalize_item(item) for item in raw if isinstance(item, dict)]
                account[kind] = {
                    "count_sample": len(items),
                    "items": [{"name": item.get("name"), "uri": item.get("uri"),
                               "favorite": item.get("favorite"),
                               "provider_instances": item.get("provider_instances", [])}
                              for item in items],
                }
            except (MAError, ValueError, TypeError) as err:
                account[kind] = {"error": type(err).__name__, "detail": str(err)[:160]}
        result["accounts"][name] = account
    return result


def recents(queue_id: str, limit: int = 40) -> list[dict[str, Any]]:
    raw = client.command(
        "music/recently_played_items",
        limit=limit,
        queue_id=queue_id,
        fully_played_only=False,
    )
    return [normalize_item(item) for item in raw if isinstance(item, dict)]


def search(query: str, provider: str = "all", limit: int = 40) -> dict[str, list[dict[str, Any]]]:
    providers = {
        "apple": ["apple_music"],
        "spotify": ["spotify"],
        "all": ["apple_music", "spotify"],
    }.get(provider, ["apple_music", "spotify"])
    raw = client.command(
        "music/search",
        search_query=query,
        media_types=["artist", "album", "track", "playlist", "radio"],
        limit=limit,
        providers=providers,
    )
    if not isinstance(raw, dict):
        return {}
    return {
        key: [normalize_item(item) for item in raw.get(key, []) if isinstance(item, dict)]
        for key in ("artists", "albums", "tracks", "playlists", "radio")
    }


def resolve_sonos_artwork(players: list[dict[str, Any]]) -> None:
    """Replace inaccessible Sonos /getaa artwork with MA-searchable track art.

    Only enrich native Sonos sources, never change queue playback or other
    players. Search is bounded to one best matching track per unique song.
    """
    cache: dict[tuple[str, str], str | None] = {}
    for player in players:
        media = player.get("current_media")
        if not isinstance(media, dict):
            continue
        original = media.get("image_url")
        if not isinstance(original, str):
            continue
        parsed = urllib.parse.urlsplit(original)
        if parsed.port != 1400 or parsed.path != "/getaa":
            continue
        title, artist = media.get("title"), media.get("artist")
        if not isinstance(title, str) or not isinstance(artist, str) or not title or not artist:
            continue
        key = (title.casefold().strip(), artist.casefold().strip())
        if key not in cache:
            cache[key] = None
            try:
                matches = search(title + " " + artist, provider="apple", limit=8).get("tracks", [])
                for track in matches:
                    if str(track.get("name", "")).casefold().strip() != key[0]:
                        continue
                    artists = track.get("artists") or []
                    if not any(
                        str(a.get("name", "")).casefold().strip() == key[1]
                        for a in artists
                    ):
                        continue
                    image = track.get("image")
                    if isinstance(image, str) and image.startswith("https://"):
                        cache[key] = image
                        break
            except (MAError, ValueError, KeyError) as err:
                print("[family-music] Native artwork lookup unavailable: " + str(err), flush=True)
        if cache[key]:
            media["image_url"] = cache[key]


class Handler(BaseHTTPRequestHandler):
    server_version = "FamilyMusic/0.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[family-music] {self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Family-Music-Version", APP_VERSION)
        if getattr(self, "request_id", None):
            self.send_header("X-Family-Music-Request-ID", self.request_id)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_artwork(self) -> None:
        """Retrieve image bytes only from the configured Music Assistant server.

        Never accept an arbitrary upstream URL: the caller supplies a relative
        image path, and the origin is always the trusted MA_URL.
        """
        from urllib.parse import urlsplit

        path = self._query().get("path", [""])[0]
        parsed = urlsplit(path)
        if (
            not path.startswith("/")
            or path.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or not (
                parsed.path == "/getaa"
                if urllib.parse.urlsplit(self.path).path == "/api/sonos-artwork"
                else any(
                    parsed.path == prefix or parsed.path.startswith(prefix + "/")
                    for prefix in ("/imageproxy", "/api/imageproxy", "/api/image")
                )
            )
        ):
            self._send_json({"error": "invalid artwork path"}, HTTPStatus.BAD_REQUEST)
            return

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        origin = MA_URL
        headers = {"Authorization": f"Bearer {MA_TOKEN}"}
        if urllib.parse.urlsplit(self.path).path == "/api/sonos-artwork":
            host = self._query().get("host", [""])[0]
            permitted = {
                (player.get("device_info") or {}).get("ip_address")
                for player in get_players()
                if (player.get("device_info") or {}).get("manufacturer") == "SONOS"
            }
            if host not in permitted or not host or parsed.path != "/getaa":
                self._send_json(
                    {"error": "unrecognized Sonos artwork source"}, HTTPStatus.BAD_REQUEST
                )
                return
            origin = f"http://{host}:1400"
            headers = {}
        cache_key = origin + path
        now = time.monotonic()
        with ARTWORK_CACHE_LOCK:
            cached = ARTWORK_CACHE.get(cache_key)
            if cached and cached[0] > now:
                content_type, content = cached[1], cached[2]
            else:
                cached = None
        if cached is not None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        request = urllib.request.Request(origin + path, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if content_type not in (
                    "image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"
                ):
                    self._send_json(
                        {"error": "upstream did not return an image"}, HTTPStatus.BAD_GATEWAY
                    )
                    return
                content = response.read(5 * 1024 * 1024 + 1)
                if len(content) > 5 * 1024 * 1024:
                    self._send_json({"error": "artwork exceeds size limit"}, HTTPStatus.BAD_GATEWAY)
                    return
        except (urllib.error.URLError, TimeoutError, ValueError):
            self._send_json({"error": "artwork unavailable"}, HTTPStatus.BAD_GATEWAY)
            return

        # A truncated JPEG can return HTTP 200 and render only its top rows.
        # Reject it rather than caching broken artwork for an hour.
        if content_type == "image/jpeg" and not (
            len(content) >= 4 and content[:2] == b"\xff\xd8" and content[-2:] == b"\xff\xd9"
        ):
            self._send_json({"error": "incomplete JPEG artwork"}, HTTPStatus.BAD_GATEWAY)
            return

        with ARTWORK_CACHE_LOCK:
            # Evict expired entries first, then oldest entries to bound memory.
            for key, entry in list(ARTWORK_CACHE.items()):
                if entry[0] <= now:
                    del ARTWORK_CACHE[key]
            while (
                ARTWORK_CACHE
                and sum(len(entry[2]) for entry in ARTWORK_CACHE.values()) + len(content)
                > ARTWORK_CACHE_MAX_BYTES
            ):
                del ARTWORK_CACHE[next(iter(ARTWORK_CACHE))]
            if len(content) <= ARTWORK_CACHE_MAX_BYTES:
                ARTWORK_CACHE[cache_key] = (now + ARTWORK_CACHE_TTL, content_type, content)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _validate_write_request(self) -> bool:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(
                {"error": "Content-Type must be application/json"},
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
            return False

        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            host = self.headers.get("Host", "")
            if not parsed.scheme or not parsed.netloc or parsed.netloc != host:
                self._send_json(
                    {"error": "Cross-origin commands are not allowed"},
                    HTTPStatus.FORBIDDEN,
                )
                return False
        return True

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError) as err:
            raise ValueError("Invalid Content-Length") from err
        if length < 0:
            raise ValueError("Invalid Content-Length")
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError("JSON body exceeds 64 KiB safety limit")
        raw = self.rfile.read(length) if length else b"{}"
        if length and len(raw) != length:
            raise ValueError("Incomplete request body")
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _acquire_request_slot(self) -> bool:
        # Artwork loads arrive in bursts when Favorites opens. Queue briefly
        # instead of returning 503 for every request above the six-slot limit.
        path = urllib.parse.urlsplit(self.path).path
        wait_seconds = 20 if path in ("/api/artwork", "/api/sonos-artwork") else 2
        if API_REQUEST_SLOTS.acquire(timeout=wait_seconds):
            return True
        self._send_json(
            {"error": "Family Music is busy; retry shortly"},
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
        return False

    def _query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def do_GET(self) -> None:  # noqa: N802
        if not self._acquire_request_slot():
            return
        try:
            self._do_GET()
        finally:
            API_REQUEST_SLOTS.release()

    def _do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/info":
                self._send_json({"frontend": "standalone", "version": APP_VERSION})
                return
            if path == "/api/health":
                players = get_players()
                self._send_json(
                    {
                        "ok": True,
                        "version": APP_VERSION,
                        "frontend": "standalone",
                        "ma_url": MA_URL,
                        "token_configured": bool(MA_TOKEN),
                        "player_count": len(players),
                    }
                )
                return
            if path in ("/api/artwork", "/api/sonos-artwork"):
                self._serve_artwork()
                return
            if path == "/api/state":
                # Keep the hot state path provider-free. Artwork recovery is
                # handled separately by the browser and cached there.
                started = time.monotonic()
                players = get_players()
                players_done = time.monotonic()
                queues = get_queues(players)
                self._send_json({"players": players, "queues": queues, "timing": {
                    "playersReadMs": round((players_done - started) * 1000),
                    "queuesReadMs": round((time.monotonic() - players_done) * 1000),
                }})
                return
            if path == "/api/native-modes":
                queue_id = self._query().get("queue_id", [""])[0]
                if queue_id:
                    _require_allowed_destination(queue_id)
                modes = sonos_playback_modes(queue_id) if queue_id else {"available": False}
                self._send_json(modes)
                return
            if path == "/api/diagnostics/favorite-ownership":
                if os.environ.get("FAMILY_MUSIC_ENABLE_OWNERSHIP_DIAGNOSTIC") != "1":
                    self._send_json({"error": "Diagnostic disabled"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json(favorite_ownership_diagnostic())
                return
            if path == "/api/favorites":
                profile = self._query().get("profile", ["all"])[0]
                self._send_json(favorites() if profile == "all" else account_favorites(profile))
                return
            if path == "/api/recents":
                query = self._query()
                queue_id = query.get("queue_id", [""])[0]
                if not queue_id:
                    self._send_json({"error": "queue_id is required"}, HTTPStatus.BAD_REQUEST)
                    return
                _require_allowed_destination(queue_id)
                self._send_json(recents(queue_id))
                return
            if path == "/api/search":
                query = self._query()
                term = query.get("q", [""])[0].strip()
                provider = query.get("provider", ["all"])[0]
                if not term:
                    self._send_json({"error": "q is required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(search(term, provider))
                return
            self._serve_static(path)
        except (MAError, ValueError, json.JSONDecodeError) as err:
            self._send_json({"error": str(err)}, HTTPStatus.BAD_GATEWAY)
        except Exception as err:
            # Sonos UPnP failures must return JSON, not drop the HTTP connection.
            print(f"[family-music] Playback error: {err}", flush=True)
            self._send_json({"error": f"Sonos playback failed: {err}"}, HTTPStatus.BAD_GATEWAY)

    def do_POST(self) -> None:  # noqa: N802
        if not self._acquire_request_slot():
            return
        try:
            self._do_POST()
        finally:
            API_REQUEST_SLOTS.release()

    def _do_POST(self) -> None:
        self.request_id = secrets.token_hex(8)
        REQUEST_CONTEXT.phase = "validation"
        path = urllib.parse.urlsplit(self.path).path
        try:
            if not self._validate_write_request():
                return
            body = self._read_json()
            candidate = body.get("request_id", "")
            if (isinstance(candidate, str) and len(candidate) <= 64
                    and candidate.replace("-", "").isalnum()):
                self.request_id = candidate
            REQUEST_CONTEXT.request_id = self.request_id
            if path == "/api/play":
                queue_id = str(body.get("queue_id") or "")
                media = body.get("media")
                if not queue_id or not media:
                    self._send_json(
                        {"error": "queue_id and media are required"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                _require_allowed_destination(queue_id)
                TEMPORARY_GROUPS.activity({queue_id})
                if isinstance(media, str) and media.startswith("sonos-favorite://"):
                    play_native_sonos_favorite(queue_id, media)
                    result = {"sonos_direct": True}
                else:
                    result = client.command(
                        "player_queues/play_media",
                        queue_id=queue_id,
                        media=media,
                    )
                    NATIVE_SONOS_SESSIONS.discard(queue_id)
                self._send_json({"ok": True, "result": result})
                return
            if path == "/api/transport":
                queue_id = str(body.get("queue_id") or "")
                action = str(body.get("action") or "")
                command = {
                    "play": "player_queues/play",
                    "pause": "player_queues/pause",
                    "next": "player_queues/next",
                    "previous": "player_queues/previous",
                }.get(action)
                if not queue_id or not command:
                    self._send_json({"error": "invalid transport command"}, HTTPStatus.BAD_REQUEST)
                    return
                diagnostic("transport_received", request_id=self.request_id,
                           selected=queue_id, action=action)
                _require_allowed_destination(queue_id)
                if action == "play":
                    TEMPORARY_GROUPS.activity({queue_id})
                if action == "previous":
                    result = previous_transport(queue_id)
                elif _native_sonos_transport(queue_id, action):
                    result = {"sonos_direct": True}
                else:
                    REQUEST_CONTEXT.phase = "ma_service"
                    diagnostic("ma_service_start", selected=queue_id,
                               target=queue_id, action=action)
                    result = client.command(command, queue_id=queue_id)
                    diagnostic("ma_service_accepted", selected=queue_id,
                               target=queue_id, action=action)
                self._send_json({"ok": True, "result": result})
                return
            if path == "/api/group-member":
                player_id = str(body.get("player_id") or "")
                member_id = str(body.get("member_id") or "")
                action = str(body.get("action") or "")
                if not player_id or not member_id:
                    self._send_json(
                        {"error": "player_id and member_id are required"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                result = change_sonos_group(player_id, member_id, action)
                self._send_json({"ok": True, **result})
                return
            if path == "/api/native-mode-control":
                queue_id = str(body.get("queue_id") or "")
                if not queue_id:
                    self._send_json({"error": "queue_id is required"}, HTTPStatus.BAD_REQUEST)
                    return
                _require_allowed_destination(queue_id)
                result = set_sonos_playback_mode(
                    queue_id, str(body.get("action") or ""), body.get("value")
                )
                self._send_json({"ok": True, "result": result})
                return
            if path == "/api/queue-control":
                queue_id = str(body.get("queue_id") or "")
                action = str(body.get("action") or "")
                value = body.get("value")
                if not queue_id:
                    self._send_json({"error": "queue_id is required"}, HTTPStatus.BAD_REQUEST)
                    return
                _require_allowed_destination(queue_id)
                if action == "seek":
                    result = client.command(
                        "player_queues/seek", queue_id=queue_id, position=int(value)
                    )
                elif action == "shuffle" and isinstance(value, bool):
                    result = client.command(
                        "player_queues/shuffle", queue_id=queue_id, shuffle_enabled=value
                    )
                elif action == "repeat" and value in {"off", "all", "one"}:
                    result = client.command(
                        "player_queues/repeat", queue_id=queue_id, repeat_mode=value
                    )
                else:
                    self._send_json({"error": "invalid queue control"}, HTTPStatus.BAD_REQUEST)
                    return
                self._send_json({"ok": True, "result": result})
                return
            if path == "/api/mute":
                player_id = str(body.get("player_id") or "")
                muted = body.get("muted")
                if not player_id or not isinstance(muted, bool):
                    self._send_json({"error": "invalid player_id or muted"}, HTTPStatus.BAD_REQUEST)
                    return
                _require_allowed_destination(player_id)
                result = client.command(
                    "players/cmd/volume_mute",
                    player_id=player_id,
                    muted=muted,
                )
                self._send_json({"ok": True, "result": result})
                return
            if path == "/api/volume":
                player_id = str(body.get("player_id") or "")
                level = int(body.get("level"))
                if not player_id or not 0 <= level <= 100:
                    self._send_json({"error": "invalid player_id or level"}, HTTPStatus.BAD_REQUEST)
                    return
                _require_allowed_destination(player_id)
                result = client.command(
                    "players/cmd/volume_set",
                    player_id=player_id,
                    volume_level=level,
                )
                self._send_json({"ok": True, "result": result})
                return
            self._send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except AuthorizationError as err:
            self._send_json({"error": str(err)}, HTTPStatus.FORBIDDEN)
        except (MAError, ValueError, TypeError, json.JSONDecodeError) as err:
            diagnostic(f"{getattr(REQUEST_CONTEXT, 'phase', 'request')}_error",
                       request_id=self.request_id, error_type=type(err).__name__)
            self._send_json({"error": str(err)[:300], "request_id": self.request_id,
                             "outcome": "rejected" if isinstance(err, (ValueError, TypeError))
                             else "unknown"},
                            HTTPStatus.BAD_GATEWAY)
        except Exception as err:
            # SoCo exceptions are not MAError. Previously these escaped the
            # handler, closed the origin connection and became a proxy 502.
            diagnostic(f"{getattr(REQUEST_CONTEXT, 'phase', 'native_request')}_error",
                       request_id=self.request_id, error_type=type(err).__name__)
            self._send_json({"error": "Sonos command failed; outcome may be unknown. "
                             "Refresh before retrying.", "request_id": self.request_id,
                             "outcome": "unknown"},
                            HTTPStatus.BAD_GATEWAY)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        if path == "/favicon.ico":
            relative = "icons/approved-v2/favicon.ico"
        if path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
            relative = "icons/approved-v2/icon-180.png"
        requested = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in requested.parents and requested != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not requested.is_file():
            requested = WEB_ROOT / "index.html"
        body = requested.read_bytes()
        content_type, _ = mimetypes.guess_type(requested.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header(
            "Cache-Control",
            (
                "no-cache"
                if requested.name in {"index.html", "service-worker.js", "manifest.webmanifest"}
                or path in {"/favicon.ico", "/apple-touch-icon.png",
                            "/apple-touch-icon-precomposed.png"}
                else "public,max-age=3600"
            ),
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FamilyMusicHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


def main() -> None:
    server = FamilyMusicHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Family Music listening on http://0.0.0.0:{PORT}", flush=True)
    print(f"Music Assistant API: {MA_URL}", flush=True)
    def stop_server(signum: int, _frame: Any) -> None:
        print(f"Family Music received signal {signum}; shutting down", flush=True)
        # shutdown() must run in a different thread from serve_forever().
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    cleanup_stop = threading.Event()
    cleanup_worker = threading.Thread(target=_idle_cleanup_worker, args=(cleanup_stop,),
                                      name="family-music-idle-cleanup", daemon=True)
    cleanup_worker.start()
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        cleanup_stop.set()
        server.server_close()
        # In-flight SOAP/MA calls are bounded; never delay container shutdown.
        cleanup_worker.join(timeout=1)
        print("Family Music shutdown complete", flush=True)


if __name__ == "__main__":
    main()
