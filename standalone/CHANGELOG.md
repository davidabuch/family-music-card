## 0.2.51

- Use observed player/coordinator state consistently for Play/Pause; retained queue state cannot override it.
- Reconcile accepted transport/volume commands with bounded fresh reads, preserving single-flight polling and all PR #99 grouping fixes.
- Keep focused volume sliders synchronized with external changes after dragging ends.
- Add bounded state timing diagnostics and a read-only native/MA commissioning sampler.
- See `docs/post-99-state-synchronization.md` for measured evidence, remaining upstream latency, commissioning and rollback.

## 0.2.50

- Fix grouped native pause/resume by verifying the selected speaker's actual Sonos coordinator.
- Return correlated JSON for native failures instead of dropping the HTTP origin connection.
- Reconcile delayed joins/leaves for 30 seconds after acceptance and report unknown outcomes accurately.
- Clean up only backend-owned temporary Sonos groups after 60 minutes of verified continuous inactivity.
- Add loaded-version/action diagnostics and advance frontend asset/cache versions.
- See `docs/issue-98-investigation.md` in the repository for ownership, restart and commissioning limits.

## 0.2.36

- Enforce configured Family Music destination scope on playback, transport, queue controls, native modes, mute, volume, recents, and returned queues.
- Reject browser write requests from a foreign Origin while preserving same-origin PWA use and non-browser local clients without an Origin header.
- Require application/json for all mutating API requests.
- Keep the login-free household PWA model unchanged; this release does not add authentication yet.
- No Sonos grouping algorithm, queue ownership, coordinator transfer, or playback-authority behavior changes.

## 0.2.35

- Make state polling single-flight so a slow Music Assistant response cannot accumulate overlapping 1.5-second polls.
- Back off state polling after failures, up to 12 seconds, while returning to the normal cadence after recovery.
- Remove provider artwork searches from the hot /api/state path and cache Sonos artwork fallback lookups in the browser.
- Preserve the selected destination if it temporarily disappears instead of silently switching rooms.
- Serialize and coalesce volume writes per speaker so delayed requests cannot overwrite a newer volume choice.
- Add bounded JSON request bodies, socket timeouts, Music Assistant response limits, and a small concurrency guard.
- Reliability-only hardening; no grouping algorithm, queue ownership, coordinator transfer, or playback-authority redesign.

## 0.2.34

- Move Mute out of the volume row so Volume Down is no longer adjacent to the mute target.
- Bottom action row is now Mute, Search, Destination, Speaker/Group.
- Slightly narrow the destination tile to make room while keeping the active room name visible.
- UI-only release; no Music Assistant, queue, grouping, or Sonos authority behavior changes.

## 0.2.33

- Replace the ambiguous square beside the current destination with a two-overlapping-speakers icon.
- Replace the ambiguous square group-control button with a single-speaker icon.
- UI-only release from the known-good 0.2.28 playback/grouping baseline; no Music Assistant, queue, or Sonos authority behavior changes.

## 0.2.28

- Keep the Group Speakers popup live after join/remove commands by polling authoritative Music Assistant topology until the requested membership change is visible.
- Re-render the open popup immediately after confirmation so a newly joined speaker can be removed without closing and reopening the panel.

## 0.2.27

- Allow removing the original Sonos coordinator when other dynamic group members remain; Music Assistant transfers the active sync session to a survivor.
- Follow the surviving speaker automatically after coordinator removal.
- Add Speaker Balance directly to dynamic groups so member prominence can be adjusted without switching destinations.
- Keep the existing Less / Neutral / More balance model and direct per-speaker volume path.

## 0.2.26

- Add dynamic ad-hoc Sonos grouping for physical speakers in the standalone Family Music app.
- Join and leave through Music Assistant's native Sonos grouping commands so playback remains one synchronized Sonos session.
- Confirm membership from authoritative Music Assistant player topology; pending UI never becomes a phantom joined state.
- Preserve permanent Music Assistant sync-group presets; this slice does not mutate their membership.

# Changelog

## 0.2.25

- Crop approved originals to full-bleed squares without borders, padding, pre-rounded corners or distortion.
- Add public Apple touch and precomposed icon routes, versioned v2 references, and refreshed PWA/favicons.
- Verify unchanged originals, pixels, dimensions, local/remote handler equality and existing security regressions.

## 0.2.24

- Use the verified approved artwork for Apple touch icons, browser favicons and 192/512px PWA icons.
- Serve identical icon bytes on local and remote origins; preserve authentication and controls.


## 0.2.19

- Show standard Repeat and Shuffle icons in their correct left/right positions.
- Cycle Repeat off, one, all and reflect active mode.
- Support Sonos-native playback modes when the Music Assistant queue is inactive.
- Grey out Previous when physical Sonos AVTransport reports it unsupported.
- Avoid using stale Music Assistant queue modes for external Sonos sources.

## 0.2.18

- Use actual physical Sonos track position for restart-first Previous behavior.
- Surface unsupported previous navigation as an error instead of silent success.
- Add a regression test for Music Assistant media elapsed time remaining zero.

## 0.2.17

- Restart the current track when Previous is pressed after three seconds of playback.
- Navigate to the preceding track when already within the first three seconds.
- Handle unsupported native Sonos Previous at the beginning of a queue without a dropped HTTP connection.
- Refresh installed PWA assets for the updated playback behavior.

## 0.2.7

- Discover native Sonos Favorites directly over the LAN using SoCo, without HA Core authentication.
- Resolve Sonos favorites to playable Music Assistant URIs and deduplicate them against MA favorites.
- Degrade gracefully when Sonos discovery is unavailable.

## 0.2.6

- Restore the animated three-bar playing indicator from the HA Family Music card in the standalone destination picker.
- Derive destination activity directly from Music Assistant player/queue state.
- Refresh playing indicators in place while the picker is open without rebuilding the menu or disturbing scrolling.

## 0.2.5

- Hide the persistent bottom navigation while speaker selection or Speaker Balance overlays are open so it can never cover selectable rows or controls.
- Restore the navigation immediately when the overlay closes.

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
