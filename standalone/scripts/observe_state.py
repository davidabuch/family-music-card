"""Bounded, read-only commissioning sampler: MA cache versus native Sonos.

Run on the LAN with SoCo installed. Never submits playback or volume commands.
Output deliberately excludes IPs, tokens, media URIs and track metadata.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime


def observe(origin: str, player_id: str) -> dict:
    import soco

    started = time.monotonic()
    with urllib.request.urlopen(origin.rstrip("/") + "/api/state", timeout=6) as response:
        data = json.load(response)
    result = {"ma_observed_at": datetime.now(UTC).isoformat(),
              "state_request_ms": round((time.monotonic() - started) * 1000)}
    player = next(p for p in data["players"] if p["player_id"] == player_id)
    queue = next((q for q in data["queues"] if q["queue_id"] == player_id), {})
    result.update(ma_player_state=player.get("state"), ma_volume=player.get("volume_level"),
                  ma_queue_state=queue.get("state"))
    speaker = soco.SoCo(player["device_info"]["ip_address"])
    # Explicit SOAP reads bypass SoCo property caches. Member volume is local;
    # transport is read, never redirected by this diagnostic tool.
    result["native_state"] = speaker.avTransport.GetTransportInfo(
        [("InstanceID", 0)], timeout=2
    ).get("CurrentTransportState")
    result["native_volume"] = speaker.renderingControl.GetVolume(
        [("InstanceID", 0), ("Channel", "Master")], timeout=2
    ).get("CurrentVolume")
    result["native_observed_at"] = datetime.now(UTC).isoformat()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Family Music LAN HTTP origin; no embedded credentials")
    parser.add_argument("player_id", help="Physical RINCON_ destination ID")
    parser.add_argument("--seconds", type=int, default=30, choices=range(1, 121))
    args = parser.parse_args()
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        try:
            print(json.dumps(observe(args.origin, args.player_id)), flush=True)
        except Exception as error:
            # Exception text may include URLs; emit only its class.
            print(json.dumps({"observation_unavailable": type(error).__name__}), flush=True)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
