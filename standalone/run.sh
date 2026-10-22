#!/bin/sh
set -eu

CONFIG=/data/options.json

export MA_URL="$(python -c 'import json; print(json.load(open("'"$CONFIG"'"))["ma_url"])')"
export MA_TOKEN="$(python -c 'import json; print(json.load(open("'"$CONFIG"'"))["ma_token"])')"
export FAMILY_MUSIC_PLAYER_IDS="$(python -c 'import json; print(json.load(open("'"$CONFIG"'")).get("player_ids", ""))')"
export FAMILY_MUSIC_EXCLUDED_PLAYLIST_INSTANCES="$(python -c 'import json; print(json.load(open("'"$CONFIG"'")).get("excluded_playlist_instances", ""))')"
export FAMILY_MUSIC_ENABLE_OWNERSHIP_DIAGNOSTIC=$(python -c "import json; print(int(json.load(open('/data/options.json')).get('enable_ownership_diagnostic', False)))")
export PORT=8099

exec python /app/server.py
