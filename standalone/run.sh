#!/bin/sh
set -eu

CONFIG=/data/options.json

export MA_URL="$(python -c 'import json; print(json.load(open("'"$CONFIG"'"))["ma_url"])')"
export MA_TOKEN="$(python -c 'import json; print(json.load(open("'"$CONFIG"'"))["ma_token"])')"
export FAMILY_MUSIC_PLAYER_IDS="$(python -c 'import json; print(json.load(open("'"$CONFIG"'")).get("player_ids", ""))')"
export PORT=8099

exec python /app/server.py
