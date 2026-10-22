"""Conservative, backend-owned leases for Family Music temporary Sonos groups.

No topology is adopted at startup. A lease is earned only by joining two solo
speakers, or extending an exactly observed existing lease, through this backend.
The injected observer must supply fresh native topology AND playback state.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

IDLE_SECONDS = 15 * 60
SETTLE_SECONDS = 60
MAX_OBSERVATION_GAP = 90
IDLE_STATES = frozenset({"paused", "idle", "off"})


@dataclass
class Observation:
    groups: dict[str, frozenset[str]]
    leaders: dict[str, str]
    states: dict[str, str]
    protected: set[str] = field(default_factory=set)

    def matches(self, members: frozenset[str]) -> bool:
        return bool(members) and not members.intersection(self.protected) and all(
            self.groups.get(member) == members
            and self.leaders.get(member) in members
            for member in members
        ) and len({self.leaders[member] for member in members}) == 1

    def idle(self, members: frozenset[str]) -> bool:
        return all(self.states.get(member) in IDLE_STATES for member in members)


@dataclass
class Lease:
    members: frozenset[str]
    previous: frozenset[str]
    settle_until: float
    confirmed: bool = False
    idle_since: float | None = None
    last_seen: float | None = None


class TemporaryGroups:
    """Policy state; callers serialize observe/change/tick with their group lock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.leases: list[Lease] = []
        self.activity_revision = 0

    def forget(self, members: set[str] | frozenset[str]) -> None:
        self.leases[:] = [lease for lease in self.leases if not lease.members.intersection(members)]

    def activity(self, members: set[str] | frozenset[str]) -> None:
        """Invalidate idle credit on play requests and observed state transitions."""
        # Increment even if cleanup has just removed its lease before sending.
        # This lets a concurrent Play request veto the next ungroup command.
        self.activity_revision += 1
        for lease in list(self.leases):
            if lease.members.intersection(members):
                lease.idle_since = None

    def changed(self, before: Observation, selected: str, member: str, action: str) -> None:
        """Called only after an accepted Family Music mutation; never on a no-op."""
        previous = before.groups.get(selected, frozenset())
        owned = next((lease for lease in self.leases if lease.members == previous), None)
        if not before.matches(previous) or (len(previous) != 1 and owned is None):
            self.forget({selected, member})
            return
        if action == "join":
            solo = frozenset({member})
            if not before.matches(solo) or member in previous:
                self.forget({selected, member})
                return
            expected = previous | solo
        else:
            if owned is None or member not in previous:
                return
            expected = previous - {member}
        self.forget(set(previous) | {member})
        total_members = sum(len(lease.members) for lease in self.leases) + len(expected)
        if 1 < len(expected) <= 16 and total_members <= 16:
            self.leases.append(Lease(expected, previous, self.clock() + SETTLE_SECONDS))

    def tick(self, observe: Callable[[frozenset[str]], Observation],
             ungroup: Callable[[str], None], diagnostic: Callable[[str, list[str]], None],
             cancelled: Callable[[], bool] = lambda: False) -> None:
        """Observe owned groups; issue each cleanup command at most once.

        Any exception or uncertain partial outcome relinquishes the lease. A new
        explicit grouping operation is needed to earn cleanup authority again.
        """
        cycle_started = self.clock()
        for lease in list(self.leases):
            if cancelled() or self.clock() - cycle_started >= 20:
                return
            # Round-robin fairness if a slow observation consumes this cycle's budget.
            self.leases.remove(lease)
            self.leases.append(lease)
            now = self.clock()
            revision = self.activity_revision
            try:
                observation = observe(lease.members)
                # Credit starts only after the complete observation has finished.
                now = self.clock()
                if not observation.matches(lease.members):
                    lease.idle_since = None
                    observed = set().union(*(observation.groups.get(member, frozenset())
                                             for member in lease.members))
                    if (not lease.confirmed and now < lease.settle_until
                            and not observed.difference(lease.members | lease.previous)):
                        continue
                    self.forget(lease.members)
                    diagnostic("ownership_relinquished", sorted(lease.members))
                    continue
                if revision != self.activity_revision:
                    lease.idle_since = None
                    continue
                lease.confirmed = True
                if lease.last_seen is not None and now - lease.last_seen > MAX_OBSERVATION_GAP:
                    lease.idle_since = None
                lease.last_seen = now
                if not observation.idle(lease.members):
                    lease.idle_since = None
                    continue
                if lease.idle_since is None:
                    lease.idle_since = now
                if now - lease.idle_since < IDLE_SECONDS:
                    continue
                # Remove the lease BEFORE sending commands: no retry after a timeout.
                self.forget(lease.members)
                remaining = lease.members
                leader = observation.leaders[next(iter(remaining))]
                member = sorted(remaining - {leader})[0]
                fresh = observe(remaining)
                if not fresh.matches(remaining) or not fresh.idle(remaining):
                    diagnostic("cleanup_aborted_revalidation", sorted(remaining))
                    continue
                if cancelled() or revision != self.activity_revision:
                    diagnostic("cleanup_aborted_activity_or_shutdown", sorted(remaining))
                    return
                ungroup(member)
                remaining = remaining - {member}
                diagnostic("idle_cleanup_member_accepted", [member])
                # One mutation per group per cycle. Let MA/native topology settle
                # before touching another member; never repeat the previous call.
                if len(remaining) > 1:
                    lease.members = remaining
                    lease.last_seen = self.clock()
                    self.leases.append(lease)
                else:
                    diagnostic("idle_cleanup_commands_accepted", sorted(lease.members))
            except Exception:
                # Fail closed on missing speakers, topology errors and partial mutations.
                self.forget(lease.members)
                diagnostic("cleanup_or_observation_failed_authority_relinquished",
                           sorted(lease.members))
