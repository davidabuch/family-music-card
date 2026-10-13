# Approved full-bleed application artwork

`approved-icon.png` and `provenance.json` are unchanged from the first icon release.
The supplied source is 1254 × 1254 pixels. `composition.json` records the new
square crop `[140, 140, 1114, 1114]` (left, top, right-exclusive, bottom-exclusive). The
crop removes the embedded surround and pre-rounded corners. It retains the
primary subject and artistic details without painting, recoloring, stretching,
compositing or adding padding. All sizes use this same crop, then Pillow 11.3.0
LANCZOS resizing. iOS alone applies the final Home Screen corner mask.

Run `python scripts/generate_icons.py`, or add `--check` to verify source hashes,
dimensions and decoded pixels. `python scripts/test_icon_artwork.py` also tests
white/black padding, rounded transparency, and legitimate white highlights.
PNG compression may differ across platforms; decoded RGBA pixels must match.

## Public discovery and cache behavior

- `/apple-touch-icon.png` and `/apple-touch-icon-precomposed.png` return the same
  approved-v2 180px PNG, including requests without query strings.
- HTML explicitly declares both with `?v=approved-v2` for changed-URL discovery.
- `/favicon.ico` returns the v2 ICO with 16/32/48px frames.
- `/icons/approved-v2/icon-16.png`, `icon-32.png`, `icon-180.png`, `icon-192.png`
  and `icon-512.png` are square PNG derivatives.
- Manifests use the v2 192/512px icons with `purpose: any`; there is no added mask.
- Existing service workers, where present, use a new cache and precache v2 paths
  plus both Apple aliases. API caching behavior is unchanged.
- Mutable discovery aliases revalidate or use no-store; no proxy changes are required.
- The older v1 files are retained for old clients, with no active v1 references
  in current HTML, manifests or service workers.

## Discovery diagnosis and limits

The first release declared a versioned PNG but did not implement either root
Apple discovery route. Pool's whitelist rejected those routes; Home's unknown
route reached its existing protected-route boundary. Music's SPA fallback
returned HTML for the missing PNG (observed through the deployed app proxy).
These are concrete discovery gaps. They do not establish why a particular
iPhone selected its saved navy letter placeholder: that device's selection
and cache state are not observable here. Successful PNG requests are not
proof of Add to Home Screen preview or installed icon acceptance.

## Local and remote acceptance

Local: http://192.168.1.125:8099
Remote: https://music.davidbuchmd.com

Tests exercise the actual static handler with these Host values and forbid
equipment requests. They verify icon bytes, types and HTTP 200 at both Apple
aliases, all five PNG paths and the ICO. Host tests do not prove live proxy
routing or Safari behavior. Family Music's local URL is the existing direct
IP/port, confirmed by the user and Family Home's existing link; no local DNS
hostname is assumed or created.

## Release and real iPhone gate

Version: 0.2.25. All three icon-correction PRs must pass their required CI
checks before any merge or add-on update. Preserve add-on options, proxies,
Cloudflare Access and origin TLS validation; do not restart Home Assistant Core.
After the verified PRs are merged, refresh Supervisor store metadata and update
only the three standalone add-ons using their normal update actions.

On an actual iPhone, open each existing URL in Safari and confirm the Share
Sheet, Add to Home Screen preview and newly installed shortcut all show the
correct distinct artwork, full-bleed with no white box or generic letter.
Launch each shortcut and verify normal authentication and navigation without
sending equipment commands. An older saved shortcut may need removal and
re-adding; do not declare success merely because a direct PNG loads.

Evidence levels: mocked/static-handler and image tests are CODE TESTED; CI must
be recorded on the exact PR heads. Live HTTP acceptance is HA OBSERVED only
after deployment checks. Actual iPhone acceptance remains NOT RUN until the
user performs and reports the device checks.
