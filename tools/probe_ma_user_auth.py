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
        status, result = request("/api", {
            "message_id": "family-music-login-probe",
            "command": "auth/login",
            "args": {"provider_id": "builtin", "username": username, "password": password,
                     "device_name": "Family Music local auth probe"},
        })
    finally:
        del password
    login = result.get("result", result) if isinstance(result, dict) else {}
    token = login.get("access_token") if isinstance(login, dict) and login.get("success") else None
    print("HTTP login status:", status)
    print("User token issued:", bool(token))
    if not token:
        raise SystemExit("Login did not yield a token; no further calls made")
    # Verify that the issued token actually resolves to the intended user.
    status, me = request("/api", {
        "message_id": "family-music-whoami-probe", "command": "auth/me", "args": {},
    }, token=token)
    identity = me.get("result") if isinstance(me, dict) else None
    verified = (status == 200 and isinstance(identity, dict) and
                str(identity.get("username", "")).casefold() == username.casefold())
    print("Authenticated identity matches entered username:", verified)
    if not verified:
        raise SystemExit("SECURITY FAIL: user identity not verified")
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
        if command == "players/all" and isinstance(items, list):
            forbidden = [p for p in items if isinstance(p, dict) and
                         "master bedroom" in str(p.get("name", "")).lower()]
            print("Master Bedroom visible to Kaelah:", bool(forbidden))
            if forbidden:
                raise SystemExit("SECURITY FAIL: restricted Master Bedroom exposed")


if __name__ == "__main__":
    main()
