# Post-PR #99 state synchronization

Baseline: merged PR #99 (`339ccaa`), standalone 0.2.50, HA card 0.3.29.
Proposed standalone release: 0.2.51. HA card and grouping backend are unchanged.

## Confirmed findings

The iPhone PWA at music.davidbuchmd.com uses standalone HTTP, not the HA card.
Sonos observations enter MA's provider/player model. `/api/state` reads MA's
`players/all`, then `player_queues/all`, serially. These are cached observations,
not live SOAP reads and not an atomic snapshot. The frontend renders after the
response, then schedules the next poll 1,500 ms later. Healthy start-to-start
spacing is therefore response duration + 1,500 ms. Failure backoff reaches 12 s.

Three frontend defects are reproduced in deterministic tests:

- `renderNow()` OR-ed queue `playing` with player `playing`, allowing a retained MA
  queue to override a newer paused player. The button handler used only the player,
  so the visible icon and the requested action could disagree.
- The one post-command `refreshState()` could reuse a poll started before command
  acceptance. No subsequent targeted observation checked delayed propagation.
- The volume thumb skipped updates whenever the range input remained focused,
  including after interaction ended. External volume could update the label while
  leaving the focused thumb stale. Local pending volume could mask observations
  for five seconds even after its write completed.

On 2026-10-22, six read-only LAN `/api/state` requests took
66.2, 61.5, 59.3, 50.9, 53.6 and 66.0 ms (median 60.4 ms).
These are origin measurements; they do not measure the iPhone/Cloudflare path.
At that latency, normal frontend polling adds approximately 0–1.57 s after MA
has the new observation, absent suspension, packet delay or backoff.

A 60-second physical sampling session collected 37 observations; origin response
latency ranged from 49 to 1,116 ms (median 61 ms). Thus the short idle sample
does not bound latency during use.

The user reported a 5–7 s Pico-to-display delay in Master Bedroom at 09:43 local.
A subsequent read-only native/MA sampler during the user's Pico actions observed:

| UTC observation | MA | Native Sonos |
| --- | --- | --- |
| 16:47:14.886 / 14.928 | idle, volume 30 | PLAYING, volume 30 |
| 16:47:16.497 / 16.542 | playing, volume 30 | PLAYING, volume 30 |
| 16:47:22.925 / 22.977 | volume 33 | volume 36 |
| 16:47:24.550 / 24.583 | volume 36 | volume 36 |

Each row's MA and SOAP reads are sequential, with separate timestamps; a change
between those reads is possible. This establishes observed disagreement and
subsequent convergence, not the precise onset or duration of provider latency.
No PWA render timestamps or Pico event timestamps were captured simultaneously.
The full 5–7 s delay cannot yet be assigned to MA, event delivery, network,
background suspension, or frontend rendering. No proxy defect was established.
No control commands, deployments or HA configuration changes were made by the agent.

## Focused correction

- One player-observation rule drives the icon, activity indicators and toggle.
  A physical synced member follows only a reciprocally observed coordinator;
  a permanent MA group retains its own player observation. Unknown/unavailable
  playback disables the toggle rather than treating a retained queue as evidence.
  Queue metadata/progress is used only when URI and playback observations agree.
  Backend native/MA command routing, PR #99 transport and all group ownership
  logic remain unchanged.
- A post-command caller can wait for an older in-flight poll and request one
  coalesced successor. All readers still share a single in-flight state request.
- Accepted Play/Pause and the final coalesced volume write trigger at most seven
  observations, spaced one second apart, with a six-second admission window.
  Reads have a six-second abort timeout; waiting for an older read and completing
  an admitted read can extend the window. No command is retried. Repeated taps
  during transport confirmation share the existing transport gate. A new volume
  intent supersedes prior volume reconciliation. Selection changes cancel it.
- Confirmation requires a successful state response with the actual expected
  value. Expiration/failure is explicitly unconfirmed and normal polling continues.
  Playback is never optimistically changed. Local volume intent clears when the
  writer settles; only active dragging protects the slider from observed updates.
- Diagnostics keep the existing 30-entry bound and record changed observations,
  queue/player disagreement, read start/spacing/round-trip time, separate MA read
  durations and correlated transport confirmation. No tokens/media URLs are added.

Continuous polling remains 1.5 s after completion. A permanent fast poll would
increase load while failing to repair MA cache latency. MA event streaming could
remove some polling delay, but requires a new long-lived upstream connection,
reconnect/snapshot ordering and fan-out lifecycle. It cannot make an event arrive
before Sonos/MA observes it. That architectural change is deferred pending this
focused patch's commissioning evidence. External Pico changes use normal polling;
there is no speculative command-based volume inference or native state overlay.

## Validation and commissioning

Python tests cover endpoint timing without provider/native discovery. Frontend
behavior tests cover retained queue disagreement, reciprocal member/permanent
MA-group observations, overlapping polls and fresh successor coalescing, delayed
propagation, rapid taps and a later reverse command, unavailable/malformed
observations, service failures, focused sliders, delayed volume observation and
selection changes. All existing grouping/transport/idle-cleanup tests are retained.
CI is authoritative for Hassfest and HACS as well as the complete existing suite.

After review, install standalone **0.2.51** and verify About & diagnostics shows
both frontend and origin 0.2.51. Refresh/reopen the iPhone PWA to activate the new
asset/cache version. HACS/card 0.3.29 does not require an update for this patch.

1. Play/Pause from Kitchen, then grouped Master Bedroom and Alexis. Verify physical
   response, observed icon transition and one command per tap. Rapid repeated taps
   during pending confirmation must not duplicate commands; reverse once confirmed.
2. Exercise Apple Music, Spotify, native radio, solo destinations and a permanent
   MA group. Check search, queue modes, skip, volume/balance, joining/leaving and
   temporary group ownership still behave as in PR #99.
3. Leave the volume control focused, then change volume with Pico. Confirm both
   label and thumb follow the actual observation after dragging stops. Repeat with
   the PWA foregrounded and after locking/unlocking the phone.
4. Run the sampler below immediately before Pico changes. Record tap time and PWA
   update time, then copy About diagnostics. Compare native observation, MA value,
   browser read timing and `state_observed` event. A native/MA gap is upstream;
   fresh MA with late browser response is polling/network; fresh browser observation
   with late visible UI is rendering/suspension. Sampling resolution limits precision.
5. Briefly disconnect the browser network: no accepted command should become an
   invented success; errors/unconfirmed status remain visible, and normal observation
   should recover. Do not induce failures while relying on playback for another task.

On a LAN workstation with SoCo installed, run this bounded read-only sampler
(use the actual LAN origin and physical player ID; no credentials in URLs):

```sh
python standalone/scripts/observe_state.py http://192.168.1.125:8099 RINCON_38420B8081B401400 --seconds 60
```

It reads SOAP transport/volume and `/api/state` every 1.5 s after each sample,
for at most 120 s plus an in-flight bounded read. It never issues control commands.
It adds temporary measurement traffic only while explicitly run.

Rollback: reinstall standalone 0.2.50, refresh/reopen the PWA, verify both versions.
No storage migration or config/entity ID change occurs. Restarting standalone
relinquishes temporary-group cleanup leases under the existing PR #99 policy;
existing physical groups stay intact. Do not merge/deploy before review.
