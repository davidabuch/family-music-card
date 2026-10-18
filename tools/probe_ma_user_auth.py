"""Read-only Music Assistant per-user authorization probe.

Run ONLY on the machine hosting Music Assistant, against loopback.
No user credentials, tokens, or playlist titles are printed or persisted.

Example:
    MA_URL=http://127.0.0.1:8095 python tools/probe_ma_user_auth.py

Prompts for Kaelah's Music Assistant username/password locally. This is
an engineering diagnostic, NOT a network-facing Family Music login.
"""
import getpass
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("MA_URL", "http://127.0.0.1:8095").rstrip("/")


def request(path, payload, token=None):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def main():
    if not (BASE.startswith("http://127.0.0.1:") or BASE.startswith("http://localhost:")):
        raise SystemExit("Refusing non-loopback Music Assistant URL")
    username = input("Music Assistant username: ").strip()
    password = getpass.getpass("Music Assistant password (not echoed): ")
    try:
        status, result = request("/auth/login", {
            "provider_id": "builtin",
            "credentials": {"username": username, "password": password},
        })
    finally:
        del password
    token = result.get("access_token") or result.get("token")
    print("HTTP login status:", status)
    print("User token issued:", bool(token))
    if not token:
        raise SystemExit("Login did not yield a token; no further calls made")
    # Read-only requests. Do not print player IDs, library titles, or secrets.
    for command in ("players/all", "music/playlists/library_items"):
        status, response = request("/api", {
            "message_id": "family-music-auth-probe",
            "command": command,
            "args": {"limit": 100, "offset": 0} if command.startswith("music/") else {},
        }, token=token)
        items = response.get("result") if isinstance(response, dict) else None
        print(command, "HTTP:", status, "items:", len(items) if isinstance(items, list) else "unknown",
              "error:", bool(response.get("error")) if isinstance(response, dict) else True)


if __name__ == "__main__":
    main()
