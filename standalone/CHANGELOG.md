# Changelog

## 0.2.4

- Align the persistent Now/Recents/Favorites/Search bar to the same centered footprint as the player instead of spanning the full browser width.
- Reduce the player footprint another ~9% so the whole control surface fits more comfortably on-screen.
- Tighten vertical spacing while preserving the existing interaction targets and functionality.

## 0.2.3

- Fix the restored bottom navigation so Now, Recents, Favorites, and Search are actually clickable.
- Remove duplicate tab-bar markup and duplicate tab-bar CSS introduced by the 0.2.2 fix.
- Add regression coverage for one tab bar only and working tab navigation wiring.

## 0.2.2

- Restore the persistent Now, Recents, Favorites, and Search navigation bar lost during the HA-card parity redesign.
- Keep navigation above the iPhone safe area while preserving the compact player footprint.

## 0.2.1

- Reduce the standalone Now Playing footprint by about 20% so the complete player fits comfortably within the screen.

## 0.2.0

- Rebuild standalone Now Playing to mirror the Home Assistant Family Music card.
- Add seek/progress, shuffle, repeat, volume nudges, destination picker, and group Speaker Balance.
- Use Music Assistant group volume for grouped destinations while preserving direct MA control.

## 0.1.2

- Keep the speaker picker stable during live polling.
- Add real optimistic mute/unmute control through Music Assistant.

## 0.1.1

- Fix Home Assistant Supervisor Web UI placeholder syntax for App Store discovery.

## 0.1.0

- Initial standalone Family Music PWA.
- Direct authenticated Music Assistant API backend.
- Player selection, Now Playing, Favorites, Recents and Search.
- Play/pause/next/previous and master volume.
- Immediate selection/transport feedback.
- Installable iPhone/iPad PWA.
