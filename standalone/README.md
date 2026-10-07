# Family Music Standalone PWA

This is the standalone, local-network Family Music app. It talks directly to Music Assistant's HTTP API and does **not** require a Home Assistant login or Home Assistant frontend session.

## Architecture

Browser / installed PWA -> Family Music local backend -> Music Assistant -> Sonos

The Music Assistant bearer token is kept on the server. It is never sent to the browser.

## Requirements

- Music Assistant 2.10+ reachable on the LAN (normally port 8095)
- a Music Assistant long-lived access token
- Python 3.13+, or Docker
- iPhone/iPad/desktop browser on the same LAN for the initial deployment

## Configuration

Environment variables:

- `MA_URL` — Music Assistant base URL, e.g. `http://192.168.1.125:8095`
- `MA_TOKEN` — Music Assistant long-lived access token
- `PORT` — Family Music web port (default `8099`)
- `FAMILY_MUSIC_PLAYER_IDS` — optional comma-separated allowlist of MA player IDs

Create a token in Music Assistant under **Settings -> Profile**. Use a dedicated Family Music account/token when practical.

## Run locally

```sh
MA_URL=http://192.168.1.125:8095 \
MA_TOKEN='replace-me' \
python standalone/server.py
```

Then open `http://<server-ip>:8099`.

## Docker

```sh
docker build -t family-music standalone
docker run --rm --network host \
  -e MA_URL=http://127.0.0.1:8095 \
  -e MA_TOKEN='replace-me' \
  family-music
```

## Install on iPhone

Open the Family Music URL in Safari, use **Share -> Add to Home Screen**, and launch the new Family Music icon. The PWA runs in standalone mode.

## Initial feature slice

- destination selection from Music Assistant players
- Now Playing
- Favorites
- Recents
- Search across Apple Music and Spotify
- play/pause/next/previous
- master volume
- immediate selection/transport feedback
- installable PWA shell

Speaker Balance and household-friendly access controls will be migrated next from the HA card.

## Security

This initial build is for a trusted home LAN. Do **not** expose port 8099 directly to the public internet. Remote access should be added later behind TLS and an authentication layer.


## Home Assistant OS installation

This repository is also a Home Assistant App repository. In **Settings -> Apps -> App store**, add:

`https://github.com/davidabuch/family-music-card`

Install **Family Music**, paste the Music Assistant long-lived token in the app configuration, and start it. The PWA is then available on port 8099 of the Home Assistant host. Home Assistant Core credentials are not used by the app.
