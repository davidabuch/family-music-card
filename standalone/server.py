"""Standalone Family Music local web/PWA server.

This service talks directly to Music Assistant's authenticated HTTP API.
It does not require Home Assistant credentials or a Home Assistant session.
"""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"

MA_URL = os.environ.get("MA_URL", "http://127.0.0.1:8095").rstrip("/")
MA_TOKEN = os.environ.get("MA_TOKEN", "")
PORT = int(os.environ.get("PORT", "8099"))
PLAYER_ALLOWLIST = {
    item.strip()
    for item in os.environ.get("FAMILY_MUSIC_PLAYER_IDS", "").split(",")
    if item.strip()
}


NATIVE_SONOS_SESSIONS: set[str] = set()


class MAError(RuntimeError):
    """Raised when Music Assistant rejects a command."""


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
                raw = response.read()
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


def _item_image(item: dict[str, Any]) -> str | None:
    image = item.get("image")
    if isinstance(image, str):
        return image
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        images = metadata.get("images")
        if isinstance(images, list):
            for entry in images:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
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


def get_queues() -> list[dict[str, Any]]:
    raw = client.command("player_queues/all")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []



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
    speaker = next(
        (sp for sp in speakers if sp.uid == player_id),
        None,
    )
    if speaker is None:
        raise ValueError("Selected destination is not a directly reachable Sonos speaker")
    entries = speaker.music_library.get_sonos_favorites() or []
    if index >= len(entries):
        raise ValueError("Sonos Favorite is no longer available; refresh Favorites")
    entry = entries[index]
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
    queues = get_queues()
    queue = next((q for q in queues if q.get("queue_id") == queue_id), None)
    if queue_id not in NATIVE_SONOS_SESSIONS and queue and queue.get("state") in {"playing", "paused"}:
        return False
    from soco.discovery import discover

    speaker = next(
        (sp for sp in (discover(timeout=3) or set()) if sp.uid == queue_id),
        None,
    )
    if speaker is None:
        return False
    {"play": speaker.play, "pause": speaker.pause,
     "next": speaker.next, "previous": speaker.previous}[action]()
    return True


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
        result[key] = [normalize_item(item) for item in raw if isinstance(item, dict)]
    result["sonos_favorites"] = native_sonos_favorites()
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


class Handler(BaseHTTPRequestHandler):
    server_version = "FamilyMusic/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[family-music] {self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, value: Any, status: int = HTTPStatus.OK) -> None:
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/health":
                players = get_players()
                self._send_json(
                    {
                        "ok": True,
                        "ma_url": MA_URL,
                        "token_configured": bool(MA_TOKEN),
                        "player_count": len(players),
                    }
                )
                return
            if path == "/api/state":
                self._send_json({"players": get_players(), "queues": get_queues()})
                return
            if path == "/api/favorites":
                self._send_json(favorites())
                return
            if path == "/api/recents":
                query = self._query()
                queue_id = query.get("queue_id", [""])[0]
                if not queue_id:
                    self._send_json({"error": "queue_id is required"}, HTTPStatus.BAD_REQUEST)
                    return
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
        path = urllib.parse.urlsplit(self.path).path
        try:
            body = self._read_json()
            if path == "/api/play":
                queue_id = str(body.get("queue_id") or "")
                media = body.get("media")
                if not queue_id or not media:
                    self._send_json(
                        {"error": "queue_id and media are required"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
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
                if _native_sonos_transport(queue_id, action):
                    result = {"sonos_direct": True}
                else:
                    result = client.command(command, queue_id=queue_id)
                self._send_json({"ok": True, "result": result})
                return
            if path == "/api/queue-control":
                queue_id = str(body.get("queue_id") or "")
                action = str(body.get("action") or "")
                value = body.get("value")
                if not queue_id:
                    self._send_json({"error": "queue_id is required"}, HTTPStatus.BAD_REQUEST)
                    return
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
                result = client.command(
                    "players/cmd/volume_set",
                    player_id=player_id,
                    volume_level=level,
                )
                self._send_json({"ok": True, "result": result})
                return
            self._send_json({"error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except (MAError, ValueError, TypeError, json.JSONDecodeError) as err:
            self._send_json({"error": str(err)}, HTTPStatus.BAD_GATEWAY)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
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
                if requested.name in {"index.html", "service-worker.js"}
                else "public,max-age=3600"
            ),
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Family Music listening on http://0.0.0.0:{PORT}", flush=True)
    print(f"Music Assistant API: {MA_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
