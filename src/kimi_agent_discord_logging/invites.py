"""Best-effort invite-use attribution without pretending Discord is exact."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from kimi_agent_module_api import InviteSnapshot


@dataclass(frozen=True, slots=True)
class InviteAttribution:
    invite: InviteSnapshot | None
    detail: str


class InviteTracker:
    """Maintain conservative, guild-local invite counter baselines."""

    def __init__(self, *, deleted_window_seconds: float = 15.0) -> None:
        self._current: dict[int, dict[str, InviteSnapshot]] = {}
        self._seeded: set[int] = set()
        self._pending: dict[int, deque[tuple[InviteSnapshot, float]]] = {}
        self._deleted: dict[int, dict[str, tuple[InviteSnapshot, float]]] = {}
        self._consumed: dict[int, dict[str, float]] = {}
        self._deleted_window_seconds = deleted_window_seconds

    def seed(self, guild_id: int, invites: tuple[InviteSnapshot, ...]) -> None:
        self._current[guild_id] = {invite.code: invite for invite in invites}
        self._seeded.add(guild_id)
        self._pending.pop(guild_id, None)

    def has_baseline(self, guild_id: int) -> bool:
        return guild_id in self._seeded

    def invalidate(self, guild_id: int) -> None:
        """Forget a baseline that may have drifted while invite fetching failed."""
        self._current.pop(guild_id, None)
        self._seeded.discard(guild_id)
        self._pending.pop(guild_id, None)
        self._deleted.pop(guild_id, None)
        self._consumed.pop(guild_id, None)

    def created(self, invite: InviteSnapshot) -> None:
        self._current.setdefault(invite.guild_id, {})[invite.code] = invite
        self._deleted.get(invite.guild_id, {}).pop(invite.code, None)

    def deleted(self, invite: InviteSnapshot, *, now: float) -> InviteSnapshot:
        previous = self._current.setdefault(invite.guild_id, {}).pop(invite.code, None)
        enriched = _prefer_known(invite, previous)
        consumed_at = self._consumed.get(invite.guild_id, {}).get(invite.code)
        if consumed_at is None or now - consumed_at > self._deleted_window_seconds:
            self._deleted.setdefault(invite.guild_id, {})[invite.code] = (enriched, now)
        return enriched

    def attribute(
        self, guild_id: int, invites: tuple[InviteSnapshot, ...], *, now: float
    ) -> InviteAttribution:
        current = {invite.code: invite for invite in invites}
        previous = self._current.get(guild_id)
        had_baseline = guild_id in self._seeded
        self._current[guild_id] = current
        self._seeded.add(guild_id)
        self._prune_deleted(guild_id, now)
        self._prune_pending(guild_id, now)
        if not had_baseline or previous is None:
            return InviteAttribution(None, "Invite counters initialized after this member joined")

        increments: list[tuple[InviteSnapshot, int]] = []
        for code, invite in current.items():
            old = previous.get(code)
            if old is None or old.uses is None or invite.uses is None:
                continue
            delta = invite.uses - old.uses
            if delta > 0:
                increments.append((invite, delta))

        pending = self._pending.setdefault(guild_id, deque())
        if len(increments) == 1:
            invite, delta = increments[0]
            pending.extend((invite, now) for _ in range(min(delta, 100)))
        elif len(increments) > 1:
            pending.clear()
            return InviteAttribution(
                None, "Several invite counters increased; attribution is ambiguous"
            )

        if pending:
            invite, _observed_at = pending.popleft()
            return InviteAttribution(invite, "Attributed by invite use-count increase")

        candidate_by_code = {
            snapshot.code: snapshot
            for snapshot, deleted_at in self._deleted.get(guild_id, {}).values()
            if now - deleted_at <= self._deleted_window_seconds
            and snapshot.max_uses is not None
            and snapshot.max_uses > 0
            and snapshot.uses is not None
            and snapshot.uses + 1 >= snapshot.max_uses
        }
        candidate_by_code.update(
            {
                snapshot.code: snapshot
                for code, snapshot in previous.items()
                if code not in current
                and snapshot.max_uses is not None
                and snapshot.max_uses > 0
                and snapshot.uses is not None
                and snapshot.uses + 1 >= snapshot.max_uses
            }
        )
        if len(candidate_by_code) == 1:
            candidate = next(iter(candidate_by_code.values()))
            self._deleted.get(guild_id, {}).pop(candidate.code, None)
            self._consumed.setdefault(guild_id, {})[candidate.code] = now
            return InviteAttribution(candidate, "Best-effort single-use invite attribution")
        return InviteAttribution(None, "No invite use counter changed")

    def _prune_deleted(self, guild_id: int, now: float) -> None:
        recent = self._deleted.get(guild_id, {})
        for code, (_invite, deleted_at) in tuple(recent.items()):
            if now - deleted_at > self._deleted_window_seconds:
                recent.pop(code, None)
        consumed = self._consumed.get(guild_id, {})
        for code, consumed_at in tuple(consumed.items()):
            if now - consumed_at > self._deleted_window_seconds:
                consumed.pop(code, None)

    def _prune_pending(self, guild_id: int, now: float) -> None:
        pending = self._pending.get(guild_id)
        if pending is None:
            return
        while pending and now - pending[0][1] > self._deleted_window_seconds:
            pending.popleft()


def _prefer_known(current: InviteSnapshot, previous: InviteSnapshot | None) -> InviteSnapshot:
    if previous is None:
        return current
    return InviteSnapshot(
        guild_id=current.guild_id,
        code=current.code,
        channel_id=current.channel_id or previous.channel_id,
        inviter_id=current.inviter_id or previous.inviter_id,
        uses=current.uses if current.uses is not None else previous.uses,
        max_uses=current.max_uses if current.max_uses is not None else previous.max_uses,
        max_age_seconds=(
            current.max_age_seconds
            if current.max_age_seconds is not None
            else previous.max_age_seconds
        ),
        temporary=current.temporary if current.temporary is not None else previous.temporary,
        created_at=current.created_at if current.created_at is not None else previous.created_at,
        expires_at=current.expires_at if current.expires_at is not None else previous.expires_at,
    )


__all__ = ["InviteAttribution", "InviteTracker"]
