"""Persistence, retention, and restart behavior."""

from __future__ import annotations

import asyncio

import pytest
from conftest import AUTHOR, GUILD, LOG_CHANNEL, SOURCE_CHANNEL, Harness
from kimi_agent_module_api.contracts import MessageRef, MessageSnapshot
from kimi_agent_module_api.events import TOPIC_MESSAGE, MessageEvent

from kimi_agent_discord_logging.guild_settings import (
    FIELD_IGNORED_CHANNELS,
    FIELD_LOG_BULK_DELETES,
    FIELD_LOG_DELETES,
    FIELD_LOG_EDITS,
    FIELD_LOGGING_CHANNEL,
    FIELD_RETENTION_DAYS,
)
from kimi_agent_discord_logging.module import PRUNE_JOB_KEY
from kimi_agent_discord_logging.snapshots import SnapshotStore

pytestmark = pytest.mark.asyncio


def _message(message_id: int, *, created_at: float) -> MessageSnapshot:
    return MessageSnapshot(
        MessageRef(GUILD, SOURCE_CHANNEL, message_id),
        AUTHOR,
        f"message-{message_id}",
        (),
        f"https://discord.com/channels/{GUILD}/{SOURCE_CHANNEL}/{message_id}",
        created_at,
    )


async def _deliver(started: Harness, message: MessageSnapshot) -> None:
    assert await started.events.deliver(TOPIC_MESSAGE, MessageEvent(message, False)) == 1


async def _drain_maintenance(started: Harness) -> None:
    while started.module._maintenance_tasks:
        await asyncio.gather(*tuple(started.module._maintenance_tasks))


async def test_lower_retention_removes_snapshots_already_older_than_limit(
    started: Harness,
) -> None:
    message = _message(1, created_at=started.clock.now)
    await _deliver(started, message)
    started.clock.now += 2 * 24 * 60 * 60

    started.guild_settings.set(GUILD, **{FIELD_RETENTION_DAYS: 1})
    await _drain_maintenance(started)

    assert await SnapshotStore(started.storage).get(message.ref) is None


async def test_increasing_retention_does_not_revive_an_already_expired_snapshot(
    started: Harness,
) -> None:
    started.guild_settings.set(GUILD, **{FIELD_RETENTION_DAYS: 1})
    await _drain_maintenance(started)
    message = _message(6, created_at=started.clock.now)
    await _deliver(started, message)
    started.clock.now += 2 * 24 * 60 * 60

    started.guild_settings.set(GUILD, **{FIELD_RETENTION_DAYS: 30})
    await _drain_maintenance(started)

    assert await SnapshotStore(started.storage).get(message.ref) is None


async def test_newly_ignored_channel_is_purged_immediately(started: Harness) -> None:
    message = _message(2, created_at=started.clock.now)
    await _deliver(started, message)

    started.guild_settings.set(GUILD, **{FIELD_IGNORED_CHANNELS: [SOURCE_CHANNEL]})
    await _drain_maintenance(started)

    assert await SnapshotStore(started.storage).get(message.ref) is None


async def test_newly_ignored_parent_purges_existing_thread_snapshots(started: Harness) -> None:
    thread_id = SOURCE_CHANNEL + 1
    message = MessageSnapshot(
        MessageRef(GUILD, thread_id, 7, parent_channel_id=SOURCE_CHANNEL),
        AUTHOR,
        "thread message",
        (),
        f"https://discord.com/channels/{GUILD}/{thread_id}/7",
        started.clock.now,
    )
    await _deliver(started, message)
    stored = await SnapshotStore(started.storage).get(message.ref)
    assert stored is not None and stored.ref.parent_channel_id == SOURCE_CHANNEL

    started.guild_settings.set(GUILD, **{FIELD_IGNORED_CHANNELS: [SOURCE_CHANNEL]})
    await _drain_maintenance(started)

    assert await SnapshotStore(started.storage).get(message.ref) is None


async def test_disabling_logging_purges_existing_snapshots(started: Harness) -> None:
    message = _message(3, created_at=started.clock.now)
    await _deliver(started, message)

    started.guild_settings.set(GUILD, **{FIELD_LOGGING_CHANNEL: None})
    await _drain_maintenance(started)

    assert await SnapshotStore(started.storage).get(message.ref) is None


async def test_invalid_guild_settings_release_module_state(started: Harness) -> None:
    message = _message(8, created_at=started.clock.now)
    await _deliver(started, message)
    assert started.module._invite_tracker.has_baseline(GUILD)

    started.guild_settings.errors[GUILD] = ("invalid settings",)
    started.guild_settings.set(GUILD)
    await _drain_maintenance(started)

    assert await SnapshotStore(started.storage).get(message.ref) is None
    assert not started.module._invite_tracker.has_baseline(GUILD)

    later = _message(9, created_at=started.clock.now)
    await _deliver(started, later)
    assert await SnapshotStore(started.storage).get(later.ref) is None


async def test_logging_changes_release_and_reseed_invite_state(started: Harness) -> None:
    assert started.module._invite_tracker.has_baseline(GUILD)
    fetches_before = len(started.discord.calls_for("fetch_invites"))

    started.guild_settings.set(GUILD, **{FIELD_LOGGING_CHANNEL: None})
    await _drain_maintenance(started)
    assert not started.module._invite_tracker.has_baseline(GUILD)

    started.guild_settings.set(GUILD, **{FIELD_LOGGING_CHANNEL: LOG_CHANNEL})
    await _drain_maintenance(started)
    assert started.module._invite_tracker.has_baseline(GUILD)
    assert len(started.discord.calls_for("fetch_invites")) == fetches_before + 1


async def test_disabling_all_message_logs_purges_unneeded_snapshots(started: Harness) -> None:
    message = _message(4, created_at=started.clock.now)
    await _deliver(started, message)

    started.guild_settings.set(
        GUILD,
        **{
            FIELD_LOG_EDITS: False,
            FIELD_LOG_DELETES: False,
            FIELD_LOG_BULK_DELETES: False,
        },
    )
    await _drain_maintenance(started)

    assert await SnapshotStore(started.storage).get(message.ref) is None
    later = _message(5, created_at=started.clock.now)
    await _deliver(started, later)
    assert await SnapshotStore(started.storage).get(later.ref) is None


async def test_restart_rebinds_one_durable_job_without_duplicate_registrations(
    started: Harness,
) -> None:
    await started.module.close()
    await started.module.close()
    assert started.events.subscriptions == ()
    assert started.interactions.commands == {}

    await started.module.start(started.ctx)

    assert len(started.events.subscriptions) == 7
    assert set(started.scheduler.jobs) == {PRUNE_JOB_KEY}
    assert len(started.interactions.commands) == 1


async def test_start_rejects_duplicate_binding_without_mutating_live_state(
    started: Harness,
) -> None:
    with pytest.raises(RuntimeError, match="already started"):
        await started.module.start(started.ctx)

    assert len(started.events.subscriptions) == 7
    assert set(started.scheduler.jobs) == {PRUNE_JOB_KEY}
