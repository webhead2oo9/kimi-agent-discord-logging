"""Invite attribution stays conservative when observations are incomplete."""

from __future__ import annotations

from kimi_agent_module_api import InviteSnapshot

from kimi_agent_discord_logging.invites import InviteTracker


def _invite(uses: int) -> InviteSnapshot:
    return InviteSnapshot(100, "welcome", inviter_id=200, uses=uses, max_uses=0)


def test_unconsumed_counter_increments_expire() -> None:
    tracker = InviteTracker(deleted_window_seconds=15)
    tracker.seed(100, (_invite(0),))

    first = tracker.attribute(100, (_invite(2),), now=10)
    stale = tracker.attribute(100, (_invite(2),), now=26)

    assert first.invite is not None and first.invite.code == "welcome"
    assert stale.invite is None


def test_invalidate_discards_an_unreliable_baseline() -> None:
    tracker = InviteTracker()
    tracker.seed(100, (_invite(0),))
    assert tracker.has_baseline(100)

    tracker.invalidate(100)
    assert not tracker.has_baseline(100)
    attribution = tracker.attribute(100, (_invite(2),), now=10)

    assert attribution.invite is None
    assert "initialized" in attribution.detail


def test_partial_create_events_do_not_become_a_trusted_baseline() -> None:
    tracker = InviteTracker()
    tracker.created(_invite(0))

    attribution = tracker.attribute(100, (_invite(1),), now=10)

    assert attribution.invite is None
    assert "initialized" in attribution.detail


def test_unknown_previous_use_count_is_not_treated_as_zero() -> None:
    tracker = InviteTracker()
    tracker.seed(
        100,
        (InviteSnapshot(100, "welcome", inviter_id=200, uses=None, max_uses=0),),
    )

    attribution = tracker.attribute(100, (_invite(1),), now=10)

    assert attribution.invite is None
    assert attribution.detail == "No invite use counter changed"


def test_disappearing_invite_with_unknown_uses_is_not_attributed() -> None:
    tracker = InviteTracker()
    tracker.seed(
        100,
        (InviteSnapshot(100, "single", inviter_id=200, uses=None, max_uses=1),),
    )

    attribution = tracker.attribute(100, (), now=10)

    assert attribution.invite is None
    assert attribution.detail == "No invite use counter changed"
