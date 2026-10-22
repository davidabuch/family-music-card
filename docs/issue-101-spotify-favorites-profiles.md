# Issue #101 — Spotify favorites profiles

## Live observations (2026-10-22; standalone 0.2.51)

The Family Music `/api/favorites` response contains two Spotify provider instances:

- `spotify--Q5Eakxzw` — **David (strong inference)**: uniquely mapped saved playlist `Liked Songs davidabuch`, plus `Workout`, `Beast Mode`, `Hot Hits USA`, and `Playlist`.
- `spotify--ooVGVwHn` — **Kaelah (strong inference)**: uniquely mapped playlist `alexis + Kaelah`, plus numerous other playlists.

The user supplied a screenshot of Music Assistant Sources listing `Spotify [davidabuch]` and `Spotify [Kaelah Buch]`. The source names match these playlist clues, but **the provider instance IDs have not been confirmed from Music Assistant's actual provider configuration API**. Verify that mapping before enabling named profiles.

The live Favorites response includes 104 playlists, 100 of them appended as saved-library playlists rather than flagged MA favorites. Counts by provider mapping: David candidate 24, Kaelah candidate 92, overlap 19. The current backend `normalize_item` already exposes `provider_instances`, and `/api/favorites` merges global MA favorites and saved playlists.

## Critical limitation

An MA item's `provider_mappings` indicates an available provider route; it does **not** prove the item is favorited by the associated account. Shared/deduplicated items may have mappings for both accounts. Do not silently claim perfect account-specific favorites from this field alone.

## Minimal proposed implementation

1. Verify mapping with MA provider configuration metadata (never expose credentials).
2. Add a remembered per-device Favorites profile selector: David, Kaelah, All. Keep All as backward-compatible fallback.
3. Scope Spotify playlist display by provider-instance mapping; preserve shared items in both profiles when both mappings exist, explicitly describe this as availability-based filtering. For albums/tracks/artists, do not assert account-specific favorite provenance unless verified.
4. Keep non-Spotify Apple Music and Sonos favorites in All, or clearly labeled shared view. Do not alter playback, volume, grouping, or transport endpoints.
5. Add tests for mapping, overlap, unknown mapping, missing metadata, and local preference persistence. Verify on live instance before production deployment.

## Status

Investigation branch only. No runtime changes or deployment. The user prefers autonomous continuation and a concrete next step at every handoff.
