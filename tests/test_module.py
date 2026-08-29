"""Message and invite logging behavior through SDK protocol fakes."""

from __future__ import annotations

import asyncio

import pytest
from conftest import (
    AUTHOR,
    GUILD,
    INVITER,
    LOG_CHANNEL,
    MEMBER,
    SOURCE_CHANNEL,
    Harness,
)
from kimi_agent_module_api import InviteSnapshot
from kimi_agent_module_api.contracts import (
    AttachmentSnapshot,
    ChannelSnapshot,
    MemberSnapshot,
    MessageRef,
    MessageSnapshot,
    OutgoingEmbed,
)
from kimi_agent_module_api.events import (
    TOPIC_INVITE_CREATE,
    TOPIC_INVITE_DELETE,
    TOPIC_MEMBER_JOIN,
    TOPIC_MESSAGE,
    TOPIC_MESSAGE_BULK_DELETE,
    TOPIC_MESSAGE_DELETE,
    TOPIC_MESSAGE_EDIT,
    InviteCreateEvent,
    InviteDeleteEvent,
    MemberJoinEvent,
    MessageBulkDeleteEvent,
    MessageDeleteEvent,
    MessageEditEvent,
    MessageEvent,
)
from kimi_agent_module_api.testing import FakeInteraction

from kimi_agent_discord_logging.guild_settings import (
    FIELD_IGNORED_CHANNELS,
    FIELD_LOG_MEMBER_JOINS,
    FIELD_LOGGING_CHANNEL,
)
from kimi_agent_discord_logging.module import PRUNE_HANDLER, PRUNE_JOB_KEY
from kimi_agent_discord_logging.snapshots import SnapshotStore

pytestmark = pytest.mark.asyncio


def _message(message_id: int = 1, *, content: str = "before") -> MessageSnapshot:
    return MessageSnapshot(
        MessageRef(GUILD, SOURCE_CHANNEL, message_id),
        AUTHOR,
        content,
        (AttachmentSnapshot(99, "proof.png", "https://cdn.example/proof.png", 10, "image/png"),),
        f"https://discord.com/channels/{GUILD}/{SOURCE_CHANNEL}/{message_id}",
        900_000.0,
        author_display_name="Ada",
    )


async def _deliver(started: Harness, topic: str, payload: object) -> None:
    assert await started.events.deliver(topic, payload) == 1


def _sent_embeds(started: Harness) -> list[OutgoingEmbed]:
    return [call.kwargs["embed"] for call in started.discord.calls_for("send_message")]


async def test_start_registers_events_command_job_and_invite_baseline(started: Harness) -> None:
    assert set(started.events.subscriptions) == {
        TOPIC_MESSAGE,
        TOPIC_MESSAGE_EDIT,
        TOPIC_MESSAGE_DELETE,
        TOPIC_MESSAGE_BULK_DELETE,
        TOPIC_INVITE_CREATE,
        TOPIC_INVITE_DELETE,
        TOPIC_MEMBER_JOIN,
    }
    assert "logging.setup" in started.interactions.commands
    assert started.interactions.commands["logging.setup"][0].min_tier == "staff"
    assert started.scheduler.jobs[PRUNE_JOB_KEY].handler == PRUNE_HANDLER
    assert len(started.discord.calls_for("fetch_invites")) == 1


async def test_uncached_edit_uses_snapshot_and_updates_it(started: Harness) -> None:
    message = _message()
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(message, author_is_bot=False))
    await _deliver(
        started,
        TOPIC_MESSAGE_EDIT,
        MessageEditEvent(message.ref, AUTHOR, None, "after", started.clock.now),
    )

    [embed] = _sent_embeds(started)
    assert embed.title == "Message edited"
    assert embed.fields[0][1] == "before"
    assert embed.fields[1][1] == "after"
    stored = await SnapshotStore(started.storage).get(message.ref)
    assert stored is not None and stored.content == "after"


async def test_edit_clears_stale_attachments_when_live_refresh_is_unavailable(
    started: Harness,
) -> None:
    message = _message()
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(message, author_is_bot=False))

    await _deliver(
        started,
        TOPIC_MESSAGE_EDIT,
        MessageEditEvent(message.ref, AUTHOR, "before", "after", started.clock.now),
    )

    stored = await SnapshotStore(started.storage).get(message.ref)
    assert stored is not None
    assert stored.content == "after"
    assert stored.attachments == ()


async def test_edit_refreshes_the_complete_live_snapshot(started: Harness) -> None:
    message = _message()
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(message, author_is_bot=False))
    refreshed = MessageSnapshot(
        message.ref,
        AUTHOR,
        "after",
        (AttachmentSnapshot(100, "new.txt", "https://cdn.example/new.txt", 20, "text/plain"),),
        message.jump_url,
        message.created_at,
        author_display_name="Ada",
        edited_at=started.clock.now,
    )
    started.discord.messages[message.ref] = refreshed

    await _deliver(
        started,
        TOPIC_MESSAGE_EDIT,
        MessageEditEvent(message.ref, AUTHOR, "before", "after", started.clock.now),
    )

    stored = await SnapshotStore(started.storage).get(message.ref)
    assert stored is not None
    assert stored.content == "after"
    assert [attachment.filename for attachment in stored.attachments] == ["new.txt"]


async def test_uncached_delete_recovers_content_and_removes_snapshot(started: Harness) -> None:
    message = _message()
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(message, author_is_bot=False))
    await _deliver(
        started,
        TOPIC_MESSAGE_DELETE,
        MessageDeleteEvent(message.ref, None, None, ()),
    )

    [embed] = _sent_embeds(started)
    assert embed.title == "Message deleted"
    assert embed.fields[0][1] == "before"
    assert "proof.png" in embed.fields[1][1]
    assert await SnapshotStore(started.storage).get(message.ref) is None


async def test_cached_empty_delete_does_not_resurrect_stale_snapshot(started: Harness) -> None:
    message = _message()
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(message, author_is_bot=False))

    await _deliver(
        started,
        TOPIC_MESSAGE_DELETE,
        MessageDeleteEvent(message.ref, AUTHOR, None, ()),
    )

    [embed] = _sent_embeds(started)
    assert embed.fields == (("Content", "*(no text content)*", False),)


async def test_bulk_delete_posts_one_summary_and_cleans_snapshots(started: Harness) -> None:
    messages = (_message(1, content="one"), _message(2, content="two"))
    for message in messages:
        await _deliver(started, TOPIC_MESSAGE, MessageEvent(message, author_is_bot=False))

    await _deliver(
        started,
        TOPIC_MESSAGE_BULK_DELETE,
        MessageBulkDeleteEvent(tuple(message.ref for message in messages)),
    )

    [embed] = _sent_embeds(started)
    assert embed.title == "Messages bulk deleted"
    assert "2" in str(embed.description)
    store = SnapshotStore(started.storage)
    for message in messages:
        assert await store.get(message.ref) is None


async def test_logging_and_ignored_channels_are_not_snapshotted(started: Harness) -> None:
    started.guild_settings.set(GUILD, **{FIELD_IGNORED_CHANNELS: [SOURCE_CHANNEL]})
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(_message(), author_is_bot=False))
    logging_message = MessageSnapshot(MessageRef(GUILD, LOG_CHANNEL, 2), AUTHOR, "log", (), "", 1.0)
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(logging_message, author_is_bot=False))

    store = SnapshotStore(started.storage)
    assert await store.get(_message().ref) is None
    assert await store.get(logging_message.ref) is None


async def test_ignored_parent_channel_excludes_its_threads(started: Harness) -> None:
    thread_id = SOURCE_CHANNEL + 1
    thread_message = MessageSnapshot(
        MessageRef(GUILD, thread_id, 3, parent_channel_id=SOURCE_CHANNEL),
        AUTHOR,
        "private thread content",
        (),
        f"https://discord.com/channels/{GUILD}/{thread_id}/3",
        1.0,
    )
    started.guild_settings.set(GUILD, **{FIELD_IGNORED_CHANNELS: [SOURCE_CHANNEL]})

    await _deliver(started, TOPIC_MESSAGE, MessageEvent(thread_message, author_is_bot=False))

    assert await SnapshotStore(started.storage).get(thread_message.ref) is None


async def test_message_work_in_different_guilds_does_not_share_a_lock(
    started: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_guild = GUILD + 1
    other_log_channel = LOG_CHANNEL + 1
    other_source_channel = SOURCE_CHANNEL + 1
    started.guild_settings.values[other_guild] = {FIELD_LOGGING_CHANNEL: other_log_channel}
    started.discord.channels[other_guild, other_log_channel] = ChannelSnapshot(
        other_guild, other_log_channel, "text", "other-audit-log"
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    original_store = started.module._store

    async def controlled_store(message: MessageSnapshot) -> None:
        if message.ref.guild_id == GUILD:
            first_started.set()
            await release_first.wait()
        await original_store(message)

    monkeypatch.setattr(started.module, "_store", controlled_store)
    first = asyncio.create_task(
        _deliver(started, TOPIC_MESSAGE, MessageEvent(_message(), author_is_bot=False))
    )
    await first_started.wait()
    other = MessageSnapshot(
        MessageRef(other_guild, other_source_channel, 4),
        AUTHOR,
        "other guild",
        (),
        "",
        1.0,
    )

    try:
        await asyncio.wait_for(
            _deliver(started, TOPIC_MESSAGE, MessageEvent(other, author_is_bot=False)),
            timeout=0.5,
        )
    finally:
        release_first.set()
        await first

    assert await SnapshotStore(started.storage).get(other.ref) is not None


async def test_invite_creation_logs_creator_and_join_uses_increment(started: Harness) -> None:
    created = InviteSnapshot(
        GUILD,
        "new-code",
        channel_id=SOURCE_CHANNEL,
        inviter_id=INVITER,
        uses=0,
        max_uses=0,
    )
    await _deliver(started, TOPIC_INVITE_CREATE, InviteCreateEvent(created))
    started.discord.invites[GUILD] = (
        InviteSnapshot(
            GUILD,
            "welcome",
            channel_id=SOURCE_CHANNEL,
            inviter_id=INVITER,
            uses=1,
            max_uses=0,
        ),
        created,
    )
    member = MemberJoinEvent(
        MemberSnapshot(GUILD, MEMBER, "New member", (), False, None, None), 1.0
    )
    await _deliver(started, TOPIC_MEMBER_JOIN, member)

    embeds = _sent_embeds(started)
    assert embeds[0].title == "Invite created"
    assert str(INVITER) in embeds[0].fields[0][1]
    assert embeds[1].title == "Member joined"
    assert "welcome" in embeds[1].fields[0][1]
    assert str(INVITER) in embeds[1].fields[1][1]
    assert "use-count increase" in str(embeds[1].footer)


async def test_two_joins_share_a_two_use_counter_delta(started: Harness) -> None:
    started.discord.invites[GUILD] = (
        InviteSnapshot(GUILD, "welcome", inviter_id=INVITER, uses=2, max_uses=0),
    )
    for user_id in (MEMBER, MEMBER + 1):
        await _deliver(
            started,
            TOPIC_MEMBER_JOIN,
            MemberJoinEvent(
                MemberSnapshot(GUILD, user_id, f"member-{user_id}", (), False, None, None),
                1.0,
            ),
        )

    embeds = _sent_embeds(started)
    assert len(embeds) == 2
    assert all("welcome" in embed.fields[0][1] for embed in embeds)


async def test_disabled_join_logging_does_not_fetch_invites(started: Harness) -> None:
    started.guild_settings.set(GUILD, **{FIELD_LOG_MEMBER_JOINS: False})
    fetches_before = len(started.discord.calls_for("fetch_invites"))

    await _deliver(
        started,
        TOPIC_MEMBER_JOIN,
        MemberJoinEvent(MemberSnapshot(GUILD, MEMBER, "member", (), False, None, None), 1.0),
    )

    assert len(started.discord.calls_for("fetch_invites")) == fetches_before
    assert _sent_embeds(started) == []


async def test_recent_single_use_invite_delete_can_attribute_join(started: Harness) -> None:
    one_use = InviteSnapshot(GUILD, "single", inviter_id=INVITER, uses=0, max_uses=1)
    await _deliver(started, TOPIC_INVITE_CREATE, InviteCreateEvent(one_use))
    await _deliver(started, TOPIC_INVITE_DELETE, InviteDeleteEvent(one_use))
    started.discord.invites[GUILD] = ()
    await _deliver(
        started,
        TOPIC_MEMBER_JOIN,
        MemberJoinEvent(MemberSnapshot(GUILD, MEMBER, "member", (), False, None, None), 1.0),
    )

    join = _sent_embeds(started)[-1]
    assert join.title == "Member joined"
    assert "single" in join.fields[0][1]
    assert "single-use" in str(join.footer)


async def test_disappeared_single_use_invite_attributes_join_before_delete_event(
    started: Harness,
) -> None:
    single = InviteSnapshot(GUILD, "single", inviter_id=INVITER, uses=0, max_uses=1)
    await _deliver(started, TOPIC_INVITE_CREATE, InviteCreateEvent(single))
    started.discord.invites[GUILD] = ()

    await _deliver(
        started,
        TOPIC_MEMBER_JOIN,
        MemberJoinEvent(MemberSnapshot(GUILD, MEMBER, "member", (), False, None, None), 1.0),
    )
    await _deliver(started, TOPIC_INVITE_DELETE, InviteDeleteEvent(single))

    join = next(embed for embed in _sent_embeds(started) if embed.title == "Member joined")
    assert "single" in join.fields[0][1]
    assert "single-use" in str(join.footer)


async def test_setup_command_proposes_logging_channel(started: Harness) -> None:
    started.discord.channels[GUILD, 999] = ChannelSnapshot(GUILD, 999, "text", "new-logs")
    _spec, setup = started.interactions.commands["logging.setup"]
    interaction = FakeInteraction(
        guild_id=GUILD,
        channel_id=SOURCE_CHANNEL,
        user_id=INVITER,
        options={"channel": 999},
    )

    await setup(interaction)

    [change] = started.proposals.changes
    assert change.target == f"guild:{GUILD}:discord_logging"
    assert "logging_channel_id: 999" in change.content
    assert interaction.last.ephemeral is True


async def test_setup_rejects_a_channel_outside_the_guild(started: Harness) -> None:
    other_channel = 999
    started.discord.channels[GUILD + 1, other_channel] = ChannelSnapshot(
        GUILD + 1, other_channel, "text", "other-guild"
    )
    _spec, setup = started.interactions.commands["logging.setup"]
    interaction = FakeInteraction(
        guild_id=GUILD,
        channel_id=SOURCE_CHANNEL,
        user_id=INVITER,
        options={"channel": other_channel},
    )

    await setup(interaction)

    assert started.proposals.changes == []
    assert "this server" in str(interaction.last.content)


@pytest.mark.parametrize(
    "channel",
    (
        ChannelSnapshot(GUILD, 998, "forum", "audit-forum"),
        ChannelSnapshot(GUILD, 999, "thread", "old-thread", archived=True),
    ),
)
async def test_setup_rejects_unsupported_log_destinations(
    started: Harness, channel: ChannelSnapshot
) -> None:
    started.discord.channels[GUILD, channel.channel_id] = channel
    _spec, setup = started.interactions.commands["logging.setup"]
    interaction = FakeInteraction(
        guild_id=GUILD,
        channel_id=SOURCE_CHANNEL,
        user_id=INVITER,
        options={"channel": channel.channel_id},
    )

    await setup(interaction)

    assert started.proposals.changes == []
    assert interaction.last.ephemeral is True


async def test_delivery_rejects_a_cross_guild_logging_channel(started: Harness) -> None:
    other_channel = 999
    started.guild_settings.set(GUILD, logging_channel_id=other_channel)
    started.discord.channels[GUILD + 1, other_channel] = ChannelSnapshot(
        GUILD + 1, other_channel, "text", "other-guild"
    )

    await _deliver(started, TOPIC_MESSAGE, MessageEvent(_message(), author_is_bot=False))
    await _deliver(
        started,
        TOPIC_MESSAGE_DELETE,
        MessageDeleteEvent(_message().ref, None, None, ()),
    )

    assert started.discord.calls_for("send_message") == []
    assert started.health.keyed[f"delivery:{GUILD}"].state == "degraded"


async def test_delivery_health_is_isolated_per_guild(started: Harness) -> None:
    other_guild = GUILD + 1
    other_log_channel = LOG_CHANNEL + 1
    started.guild_settings.values[other_guild] = {FIELD_LOGGING_CHANNEL: other_log_channel}
    started.discord.channels[other_guild, other_log_channel] = ChannelSnapshot(
        other_guild, other_log_channel, "text", "other-audit-log"
    )
    started.guild_settings.values[GUILD][FIELD_LOGGING_CHANNEL] = 999

    await started.module._send_log(GUILD, OutgoingEmbed(title="failure"))
    await started.module._send_log(other_guild, OutgoingEmbed(title="success"))

    assert started.health.keyed[f"delivery:{GUILD}"].state == "degraded"
    assert f"delivery:{other_guild}" not in started.health.keyed


async def test_prune_job_removes_expired_snapshots(started: Harness) -> None:
    message = _message()
    await _deliver(started, TOPIC_MESSAGE, MessageEvent(message, author_is_bot=False))
    started.clock.now += 31 * 24 * 60 * 60

    assert await started.scheduler.run_due(now=10**12) == 1
    assert await SnapshotStore(started.storage).get(message.ref) is None
