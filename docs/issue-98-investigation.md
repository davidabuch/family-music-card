# Issue #98: grouped transport, confirmation and temporary-group cleanup

## Scope and evidence

Base: `9b4173896ad424f7a6c7a6184bd74f012ac4caa8` (PR #97, HA card 0.3.28).
PR #94 is not incorporated. The existing dashboard/entity/config-entry IDs are unchanged.

Read-only production inspection on 2026-10-22 established a different execution
path from the initial hypothesis:

- Cloudflared's ingress maps `music.davidbuchmd.com` directly to the Family Music
  standalone HTTP origin on port 8099. That path does not call the HA card's
  `_resolveTransportTarget`, HA websocket endpoint, or HA `media_player` service.
- The installed Family Music App is **0.2.49**; the HA card is separately versioned
  **0.3.28**. Updating HACS alone cannot change the standalone origin.
- Standalone origin logs repeatedly show `SoCoSlaveException: The method or
  property "pause" can only be called/used on the coordinator in a group`, at
  `_native_sonos_transport`, `getattr(speaker, action)()` (old server.py:507).
- The exception escapes `_do_POST` because its exception list does not include
  SoCo exceptions. The request thread closes without an HTTP response.
- Cloudflared logs `Unable to reach the origin service ... EOF` for
  `https://music.davidbuchmd.com/api/transport`, including 15:54:26/27 UTC.
- HA system logs contain no Family Music diagnostic records. This is expected
  for standalone requests: the HA `client_diagnostic` handler is not in this path.

Historical requests have no correlation IDs, so individual old log entries cannot
be joined one-to-one. The route, stack trace and proxy EOF establish the failure
mechanism. Targeted regressions reproduce the exception and escaping handler on
the base code; the repaired real loopback HTTP handler returns correlated JSON.
No physical playback, configuration changes, restarts or deployments were performed.

## Confirmed causes and focused repairs

### Pause / resume

Native fallback addressed the selected physical member, although SoCo requires
its coordinator. The fix uses that selected speaker's observed native group,
verifies membership and coordinator identity, checks destination scope, and sends
one transport command. Unknown/inconsistent topology fails visibly; it never
falls back to an arbitrary speaker. Native Previous uses the same verification.
A grouped idle member can resume its coordinator; the existing solo-idle MA
resume path and MA-managed queue path are preserved.

Unhandled native exceptions now become bounded JSON errors with `request_id`,
`outcome: unknown`, and origin version/reference headers. They do not become an
origin EOF. Genuine MA/native failures remain errors; commands are not retried.
Cloudflare routing/authentication and Nginx settings are unchanged. There is no
evidence that websocket proxy configuration caused this HTTP request failure.

The HA card keeps its existing MA-coordinator route. Its Play/Pause chooses the
service from the resolved destination's state, rejects unavailable topology, and
prevents concurrent duplicate taps. Service and topology phases are separately
visible and correlated. These are covered by simulated service/topology tests;
this investigation does not claim that HA routing caused the observed 502.

### Group confirmation

Both frontends used ten polls separated by 180 ms: about **1.8 seconds plus
request latency**, not the seven seconds suggested by their pending UI deadline.
Standalone then displayed “not confirmed.” Successful POST responses in the
origin logs and the reported later physical join are consistent with this race.
The exact physical propagation delay was not measured during this investigation.

The patch starts a bounded 30-second reconciliation window **after acceptance**,
uses 1.5-second observation spacing and no duplicate mutation, and distinguishes:

1. sending / accepted and propagating;
2. positively confirmed topology;
3. explicit backend rejection;
4. unknown outcome after unavailable observations, lost responses or expiry.

Missing topology is never proof of a successful leave. Standalone confirmation
requires reciprocal member/coordinator topology, rather than just a cached member
list. A failed refresh cannot confirm anything from old state. Late positive
standalone state can reconcile an unknown result through the existing state poll.
Coordinator removal follows a verified survivor, without overwriting a deliberate
subsequent user destination selection. The backend no longer guesses a successor
when the immediate post-command observation is still old.

Control responses are bounded at the client. Timing out stops waiting; it does
not claim to cancel an already-submitted command. The UI reports uncertainty and
never automatically resubmits a mutation. The HA menu can be reopened to refresh
an unknown outcome; it adds no always-running group polling.

## Idle cleanup design and ownership

The owner is the **standalone App backend**, the component proven to create the
reported groups. It runs on HAOS independently of HA Core and the phone. Moving
its ownership to HA Core would require a new cross-service credential/bridge;
this change deliberately does not introduce one. HA-card/other-app-created groups
are not adopted. Cleanup of those unrelated groups is outside this lease scope.

A lease is earned only after this backend accepts a join between two verified
solo physical Sonos speakers, or extends an already-owned exact group. An
already-existing group or no-op join does not earn ownership. An observation
failure prevents lease acquisition without breaking manual grouping.

The backend checks fresh native `GetZoneGroupState` and transport state from each
configured MA device address, and requires agreement with current MA topology.
Bonded satellites are included so a stereo pair is not misclassified as solo.
A speaker currently sourced/synced from a permanent MA virtual group is protected.

- `paused`, `idle`, `off`: earn idle credit only when **both** MA state and native
  SOAP transport state positively indicate inactivity and native status is OK.
- `playing`, buffering, transitioning, unknown, unavailable, inconsistent topology
  or any unrecognized state: do not earn idle credit. Known activity resets it.
- Play/play-media requests immediately invalidate idle credit, even before state
  catches up. An activity revision vetoes cleanup racing such a request.
- One worker checks owned groups every 30 seconds. There is no network traffic
  from it when no leases exist, no browser timer and no added HA API polling.
- The clock is monotonic. An observation gap over 90 seconds resets idle credit.
  Cleanup is never earlier than 60 minutes of observed continuous inactivity;
  scheduling/observation latency can make it later.
- Exact membership changes not initiated here relinquish ownership. Coordinator
  identity can change only if the complete observed owned member set is unchanged.
  Manual leave relinquishes the lease; surviving subsets are not silently adopted.
- Immediately before each cleanup command, fresh MA/native topology, scope and
  **every remaining member's** playback state are revalidated. If anything is
  ambiguous or active, do not ungroup.
- Grouping/cleanup share a lock. Cleanup removes one non-coordinator member per
  group per cycle through the **existing MA ungroup command**. Larger groups are
  revalidated on later cycles, allowing topology propagation between removals.
- Each removal is submitted at most once. A timeout, partial failure or unexpected
  topology relinquishes authority; no blind mutation retries. Diagnostics say
  “command accepted,” not “physical cleanup confirmed.” A new explicit solo join
  can earn authority again.
- Storage is bounded to 16 tracked physical members. Work is round-robin, has a
  20-second admission budget per cycle (an already-running bounded observation
  may finish later), explicit two-second SOAP timeouts and the existing MA timeout.
  Shutdown stops scheduling and vetoes further commands after in-flight reads.

### Restart and limits

HA Core restarts do not reset the standalone worker. App/container restarts,
updates and process restarts deliberately discard leases and idle deadlines.
Groups are never reconstructed from observed topology alone: another application
may have changed them while this owner was offline. This is the permitted
fail-safe restart behavior, rather than unsafe restoration of stale ownership.
After an App restart, manually dissolve/recreate a temporary group to re-arm it.

The observer can act only on observed evidence. External playback or a group
change that starts and ends entirely between observations is not reconstructible.
No distributed Sonos/MA API provides an atomic “ungroup only if still idle”
transaction: the final read-to-command interval cannot be eliminated. Fresh native
reads, immediate local activity vetoes, one mutation per cycle and fail-closed
handling minimize that interval without claiming an impossible hardware guarantee.

## Diagnostics and version verification

Standalone **0.2.50**: open **About & diagnostics**. It shows loaded frontend and
backend versions, plus a bounded 30-entry action trace. `/api/info` is a read-only
backend identity probe. Control actions carry a correlation ID; JSON responses
have `X-Family-Music-Version` and `X-Family-Music-Request-ID`. Matching origin logs
show received, native/MA service start, accepted and exception class. Non-JSON
HTTP errors are explicitly identified as proxy/non-JSON responses. No tokens,
media URLs, cookies or catalog contents are added to diagnostic logs.

HA card **0.3.29**: About & diagnostics shows the frontend version and diagnostic
handshake status. The registered `family_music/client_diagnostic` command returns
its backend version; tests execute registration and the handler. Failures warn in
the local console and remain visible in the card instead of being silently
swallowed. Logs include correlation IDs, selected/resolved entities and phases;
server logging is bounded to 60 diagnostic records/minute. Diagnostic failure
cannot block the playback command. MA entry selection is independently tested;
the Family Music entry is not substituted for the dashboard's MA entry.

Standalone asset query strings and the worker cache version now advance together;
they had remained at 0.2.40 despite the deployed 0.2.49 backend.

## Release / rollback notes

- Review only: standalone App 0.2.50; HA integration/card 0.3.29.
- Update the standalone App to repair `music.davidbuchmd.com`; a HACS-only update
  cannot repair this origin. Update HACS separately for the HA-card improvements.
- Do not change dashboard config, entity IDs, MA config-entry ID, Cloudflare Access,
  tunnel routing, TLS or proxy security settings.
- Existing groups are intentionally not adopted on update. Recreate the test group
  through Family Music after commissioning the new App.
- Roll back using the previous App/integration release if necessary, preserving
  options. Restart discards new cleanup leases; rollback does not itself ungroup
  speakers. No persisted ownership schema or dashboard migration needs undoing.

## Physical commissioning checklist (not run)

1. Open the iPhone PWA at its existing URL. Confirm About shows standalone
   frontend/backend **0.2.50**, not only HA card **0.3.29**. Confirm normal Access
   authentication, catalog browsing/search, Apple/Spotify playback and favorites.
2. Start Kitchen, add Master Bedroom, and allow delayed propagation. Verify
   accepted/pending becomes confirmed without a false error or duplicate join.
3. Pause/resume with Kitchen selected, then Master Bedroom selected; repeat with
   Alexis. Record the action reference and verify origin logs target the native
   coordinator once. Check volume/balance, skip/Previous and permanent MA groups.
4. Remove a member and the coordinator separately. Verify actual membership and
   the surviving destination, including a destination switch while waiting.
5. Create a fresh owned temporary group, pause it and close/lock the PWA. At 59
   minutes it must remain grouped; after the full observed hour plus worker margin
   verify independent rooms. Resume Kitchen and confirm other rooms stay silent.
6. Repeat with playback resumed before expiry: no cleanup. During a cleanup test,
   resume playback and verify remaining removals stop. A command already submitted
   to hardware cannot be recalled.
7. Modify a group in Sonos, test an unrelated group and a permanent MA group: none
   may be dismantled. Test HA Core restart and App restart separately; the latter
   must drop ownership rather than ungroup an inherited group.
8. If another 502 occurs, collect frontend/backend versions, action reference,
   response status/content type/origin headers, and same-time origin/Cloudflared
   entries. Origin JSON with an exception class differs from a proxy-generated EOF
   response. Do not repeatedly retry an unknown mutation.
