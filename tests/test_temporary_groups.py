"""Controllable-clock safety tests: no hardware and no real-time delays."""

import importlib.util
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "standalone/temporary_groups.py"
spec = importlib.util.spec_from_file_location("temporary_group_policy", PATH)
policy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = policy
spec.loader.exec_module(policy)
Observation, TemporaryGroups = policy.Observation, policy.TemporaryGroups


class Rig:
    def __init__(self, ids="AB"):
        self.now = 0
        self.manager = TemporaryGroups(lambda: self.now)
        self.groups = {key: frozenset({key}) for key in ids}
        self.leaders = {key: key for key in ids}
        self.states = dict.fromkeys(ids, "paused")
        self.protected = set()
        self.commands = []
        self.events = []

    def observe(self, _members):
        return Observation(dict(self.groups), dict(self.leaders), dict(self.states), self.protected)

    def set_group(self, ids, leader=None):
        for key in ids:
            self.groups[key] = frozenset(ids)
            self.leaders[key] = leader or ids[0]

    def join(self, selected="A", member="B", converge=True):
        before = self.observe(None)
        self.manager.changed(before, selected, member, "join")
        if converge:
            self.set_group(sorted(before.groups[selected] | {member}))
        self.tick()

    def ungroup(self, member):
        self.commands.append(member)
        rest = self.groups[member] - {member}
        self.set_group(sorted(rest))
        self.set_group(member)

    def tick(self, observer=None, ungroup=None):
        self.manager.tick(observer or self.observe, ungroup or self.ungroup,
                          lambda phase, members: self.events.append((phase, members)))

    def advance(self, seconds):
        target = self.now + seconds
        while self.now < target:
            self.now = min(target, self.now + 30)
            self.tick()


def test_59_minutes_preserves_group_60_cleans_once():
    rig = Rig()
    rig.join()
    rig.advance(59 * 60)
    assert rig.commands == []
    rig.advance(60)
    assert rig.commands == ["B"]
    assert all(len(group) == 1 for group in rig.groups.values())
    rig.advance(3600)
    assert rig.commands == ["B"]


def test_resume_resets_continuous_idle_period():
    rig = Rig()
    rig.join()
    rig.advance(3590)
    rig.states["B"] = "playing"
    rig.tick()
    rig.states["B"] = "paused"
    rig.tick()
    rig.advance(3590)
    assert not rig.commands
    rig.advance(10)
    assert rig.commands == ["B"]


@pytest.mark.parametrize(
    "state", ["unknown", "unavailable", "buffering", "transitioning", "playing"]
)
def test_ambiguous_and_active_states_never_earn_idle_credit(state):
    rig = Rig()
    rig.join()
    rig.advance(3590)
    rig.states["B"] = state
    rig.advance(120)
    assert not rig.commands
    assert rig.manager.leases[0].idle_since is None


def test_play_request_resets_before_state_catches_up():
    rig = Rig()
    rig.join()
    rig.advance(3590)
    rig.manager.activity({"B"})
    rig.advance(30)
    assert not rig.commands


def test_playback_resumes_during_revalidation():
    rig = Rig()
    rig.join()
    rig.advance(3570)
    rig.now = 3600
    calls = 0

    def observe(members):
        nonlocal calls
        calls += 1
        if calls == 2:
            rig.states["A"] = "playing"
        return rig.observe(members)

    rig.tick(observer=observe)
    assert not rig.commands
    assert rig.events[-1][0] == "cleanup_aborted_revalidation"


def test_coordinator_change_with_exact_members_is_safe():
    rig = Rig()
    rig.join()
    rig.set_group("AB", leader="B")
    rig.advance(3600)
    assert rig.commands == ["A"]


def test_external_membership_change_relinquishes_ownership():
    rig = Rig("ABC")
    rig.join()
    rig.set_group("ABC")
    rig.tick()
    assert not rig.manager.leases
    rig.advance(7200)
    assert not rig.commands


def test_unrelated_and_permanent_groups_are_never_adopted():
    rig = Rig("ABCD")
    rig.set_group("AB")  # Created by another app.
    rig.join("A", "C")
    assert not rig.manager.leases
    rig = Rig()
    rig.protected.add("A")
    rig.join()
    assert not rig.manager.leases


def test_restart_drops_authority_and_deadline():
    rig = Rig()
    rig.join()
    rig.advance(3500)
    rig.manager = TemporaryGroups(lambda: rig.now)
    rig.advance(7200)
    assert not rig.commands


def test_observation_failure_relinquishes_without_mutation():
    rig = Rig()
    rig.join()

    def unavailable(_members):
        raise TimeoutError("offline")

    rig.tick(observer=unavailable)
    rig.advance(7200)
    assert not rig.commands
    assert not rig.manager.leases


def test_cleanup_failure_is_not_retried_and_new_explicit_join_can_rearm():
    rig = Rig()
    rig.join()
    rig.advance(3570)
    rig.now = 3600

    def failure(member):
        rig.commands.append(member)
        raise TimeoutError("outcome unknown")

    rig.tick(ungroup=failure)
    rig.advance(7200)
    assert rig.commands == ["B"]
    rig.set_group("A")
    rig.set_group("B")
    rig.join()
    rig.advance(3600)
    assert rig.commands == ["B", "B"]


def test_multiple_independent_groups():
    rig = Rig("ABCD")
    rig.join("A", "B")
    rig.join("C", "D")
    rig.advance(3600)
    assert rig.commands == ["B", "D"]


def test_observation_gap_resets_idle_credit():
    rig = Rig()
    rig.join()
    rig.advance(3500)
    rig.now += 300
    rig.tick()
    assert not rig.commands
    assert rig.manager.leases[0].idle_since == rig.now


def test_pending_join_requires_positive_confirmation_and_expires():
    rig = Rig()
    rig.join(converge=False)
    assert not rig.manager.leases[0].confirmed
    rig.advance(90)
    assert not rig.manager.leases
    assert not rig.commands


def test_resume_between_cleanup_members_stops_remaining_commands():
    rig = Rig("ABC")
    rig.join()
    rig.join("A", "C")

    def remove_then_resume(member):
        rig.ungroup(member)
        rig.states["A"] = "playing"

    rig.advance(3570)
    rig.now += 30
    rig.tick(ungroup=remove_then_resume)
    assert rig.commands == ["B"]
    assert rig.groups["C"] == frozenset("AC")


def test_play_request_during_cleanup_vetoes_command_even_if_state_is_stale():
    rig = Rig()
    rig.join()
    rig.advance(3570)
    rig.now = 3600
    count = 0

    def observe(members):
        nonlocal count
        count += 1
        if count == 2:
            rig.manager.activity({"B"})
        return rig.observe(members)

    rig.tick(observer=observe)
    assert not rig.commands


def test_shutdown_during_revalidation_prevents_command():
    rig = Rig()
    rig.join()
    rig.advance(3570)
    rig.now = 3600
    stopped = False
    count = 0

    def observe(members):
        nonlocal stopped, count
        count += 1
        if count == 2:
            stopped = True
        return rig.observe(members)

    rig.manager.tick(observe, rig.ungroup, lambda *_: None, lambda: stopped)
    assert not rig.commands


def test_larger_group_cleans_one_member_per_cycle_after_topology_settles():
    rig = Rig("ABC")
    rig.join()
    rig.join("A", "C")
    rig.advance(3600)
    assert rig.commands == ["B"]
    rig.advance(30)
    assert rig.commands == ["B", "C"]
    assert all(len(group) == 1 for group in rig.groups.values())


def test_idle_credit_starts_after_observation_completes():
    rig = Rig()
    before = rig.observe(None)
    rig.manager.changed(before, "A", "B", "join")
    rig.set_group("AB")

    def slow_observe(members):
        rig.now += 5
        return rig.observe(members)

    rig.tick(observer=slow_observe)
    assert rig.manager.leases[0].idle_since == 5
