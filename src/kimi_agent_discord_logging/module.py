"""Lifecycle and event handlers for Discord logging."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from kimi_agent_module_api import (
    ModuleRuntimeContext,
    ProposalActor,
    ProposalError,
    ScopedModuleMigration,
    render_guild_settings,
)
from kimi_agent_module_api.contracts import (
    CommandOption,
    CommandSpec,
    Event,
    JobRun,
    MessageRef,
    MessageSnapshot,
    ModuleInteraction,
    OutgoingEmbed,
    Registration,
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

from kimi_agent_discord_logging.guild_settings import (
    FIELD_IGNORE_BOTS,
    FIELD_IGNORED_CHANNELS,
    FIELD_LOG_BULK_DELETES,
    FIELD_LOG_DELETES,
    FIELD_LOG_EDITS,
    FIELD_LOG_INVITE_CREATE,
    FIELD_LOG_INVITE_DELETE,
    FIELD_LOG_MEMBER_JOINS,
    FIELD_LOGGING_CHANNEL,
    FIELD_RETENTION_DAYS,
)
from kimi_agent_discord_logging.invites import InviteAttribution, InviteTracker
from kimi_agent_discord_logging.migrations import MIGRATIONS
from kimi_agent_discord_logging.renderer import (
    bulk_delete_embed,
    invite_create_embed,
    invite_delete_embed,
    member_join_embed,
    message_delete_embed,
    message_edit_embed,
)
from kimi_agent_discord_logging.snapshots import SnapshotStore

log = logging.getLogger(__name__)

MODULE_NAME = "discord_logging"
COMMAND_GROUP = "logging"
PRUNE_HANDLER = "prune_snapshots"
PRUNE_JOB_KEY = "snapshot_prune"
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60
PRUNE_JITTER_SECONDS = 15 * 60
DAY_SECONDS = 24 * 60 * 60


class DiscordLoggingModule:
    """Coordinate Discord events, short-lived snapshots, and audit delivery."""

    scoped_migrations: Sequence[ScopedModuleMigration] = MIGRATIONS

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._ctx: ModuleRuntimeContext | None = None
        self._snapshots: SnapshotStore | None = None
        self._registrations: list[Registration] = []
        self._maintenance_tasks: set[asyncio.Task[None]] = set()
        self._message_locks: dict[int, asyncio.Lock] = {}
        self._invite_locks: dict[int, asyncio.Lock] = {}
        self._invite_tracker = InviteTracker()
        self._events_seen = 0
        self._logs_sent = 0
        self._logs_failed = 0
        self._snapshots_missed = 0
        self._snapshots_pruned = 0

    async def start(self, ctx: ModuleRuntimeContext) -> None:
        if self._ctx is not None:
            raise RuntimeError(f"{MODULE_NAME} is already started")
        self._ctx = ctx
        self._snapshots = SnapshotStore(ctx.storage)
        self._registrations.extend(
            (
                ctx.events.subscribe(TOPIC_MESSAGE, self._on_message),
                ctx.events.subscribe(TOPIC_MESSAGE_EDIT, self._on_message_edit),
                ctx.events.subscribe(TOPIC_MESSAGE_DELETE, self._on_message_delete),
                ctx.events.subscribe(TOPIC_MESSAGE_BULK_DELETE, self._on_bulk_delete),
                ctx.events.subscribe(TOPIC_INVITE_CREATE, self._on_invite_create),
                ctx.events.subscribe(TOPIC_INVITE_DELETE, self._on_invite_delete),
                ctx.events.subscribe(TOPIC_MEMBER_JOIN, self._on_member_join),
                ctx.interactions.add_command(
                    CommandSpec(
                        name="setup",
                        description="Propose the channel that receives Discord audit logs.",
                        group=COMMAND_GROUP,
                        group_description="Configure Discord audit logging.",
                        min_tier="staff",
                        options=(
                            CommandOption("channel", "channel", "Logging channel.", required=True),
                        ),
                    ),
                    self._command_setup,
                ),
            )
        )
        if ctx.guild_settings is not None:
            self._registrations.append(ctx.guild_settings.on_change(self._on_guild_change))
        ctx.scheduler.register(PRUNE_HANDLER, self._prune_snapshots)
        await ctx.scheduler.run_every(
            PRUNE_JOB_KEY,
            PRUNE_INTERVAL_SECONDS,
            PRUNE_HANDLER,
            jitter_seconds=PRUNE_JITTER_SECONDS,
        )
        await self._reconcile_snapshots()
        await self._validate_logging_channels()
        await self._seed_invites()
        self._report_health()

    async def close(self) -> None:
        for registration in reversed(self._registrations):
            registration.close()
        self._registrations.clear()
        tasks = tuple(self._maintenance_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._maintenance_tasks.clear()
        self._message_locks.clear()
        self._invite_locks.clear()
        self._invite_tracker = InviteTracker()
        self._snapshots = None
        self._ctx = None

    async def _on_message(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, MessageEvent):
            return
        message = payload.message
        async with self._message_lock_for(message.ref.guild_id):
            self._events_seen += 1
            if not self._tracks_message(message.ref):
                return
            await self._store(message)
            self._report_health()

    async def _on_message_edit(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, MessageEditEvent):
            return
        ref = payload.ref
        async with self._message_lock_for(ref.guild_id):
            self._events_seen += 1
            if not self._tracks_message(ref):
                return
            ctx, snapshots = self._require_started()
            stored = await snapshots.get(ref)
            before = payload.before_content
            if before is None and stored is not None:
                before = stored.content
            current: MessageSnapshot | None = None
            after = payload.after_content
            try:
                current = await ctx.discord.fetch_message(ref)
            except Exception:
                log.exception("Could not fetch edited message %s", ref.message_id)
            if current is not None and after is None:
                after = current.content
            effective_ref = ref
            if stored is not None:
                effective_ref = stored.ref
            if current is not None:
                effective_ref = current.ref
            if not self._tracks_message(effective_ref):
                if stored is not None:
                    await snapshots.delete(ref)
                return
            if self._ignores_bots(ref.guild_id) and (
                (stored is not None and stored.author_is_bot)
                or (current is not None and current.author_is_bot)
            ):
                if current is not None:
                    await self._store(current)
                elif stored is not None and after is not None:
                    await snapshots.update_after_edit(
                        ref,
                        after,
                        attachments=(),
                        edited_at=payload.edited_at or self._clock(),
                        expires_at=self._expiry(ref.guild_id),
                    )
                self._report_health()
                return
            if after is None:
                self._snapshots_missed += 1
                self._report_health()
                return
            if current is not None:
                await self._store(current)
            elif stored is not None:
                # MessageEditEvent intentionally carries text only. If the
                # live fetch failed, retaining pre-edit attachment metadata
                # could resurrect a file that the author removed.
                await snapshots.update_after_edit(
                    ref,
                    after,
                    attachments=(),
                    edited_at=payload.edited_at or self._clock(),
                    expires_at=self._expiry(ref.guild_id),
                )
            if before is None or before == after:
                if before is None:
                    self._snapshots_missed += 1
                self._report_health()
                return
            values = self._guild_values(ref.guild_id)
            if not values.get(FIELD_LOG_EDITS, True):
                return
            author_id = payload.author_id
            if author_id is None and current is not None:
                author_id = current.author_id
            if author_id is None and stored is not None:
                author_id = stored.author_id
            author_name = (
                current.author_display_name
                if current is not None
                else stored.author_display_name
                if stored is not None
                else ""
            )
            await self._send_log(
                effective_ref.guild_id,
                message_edit_embed(
                    effective_ref,
                    author_id=author_id,
                    author_name=author_name,
                    before=before,
                    after=after,
                ),
            )

    async def _on_message_delete(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, MessageDeleteEvent):
            return
        ref = payload.ref
        async with self._message_lock_for(ref.guild_id):
            self._events_seen += 1
            _ctx, snapshots = self._require_started()
            stored = await snapshots.get(ref)
            event_author_is_bot = getattr(payload, "author_is_bot", None)
            if self._ignores_bots(ref.guild_id) and (
                event_author_is_bot is True or (stored is not None and stored.author_is_bot)
            ):
                await snapshots.delete(ref)
                self._report_health()
                return
            effective_ref = stored.ref if stored is not None else ref
            if not self._tracks_message(effective_ref):
                if stored is not None:
                    await snapshots.delete(ref)
                return
            values = self._guild_values(ref.guild_id)
            if not values.get(FIELD_LOG_DELETES, True):
                await snapshots.delete(ref)
                return
            # Core supplies an author for the rich cached-delete event and no
            # author for the raw fallback. An empty rich payload is therefore
            # authoritative; falling back to an older snapshot could resurrect
            # content or attachments that were removed in a later edit.
            has_cached_message = payload.author_id is not None
            author_id = payload.author_id or (stored.author_id if stored else None)
            author_name = stored.author_display_name if stored else ""
            content = payload.cached_content
            if content is None and has_cached_message:
                content = ""
            elif content is None and stored is not None:
                content = stored.content
            attachments = payload.cached_attachments
            if not has_cached_message and not attachments and stored is not None:
                attachments = stored.attachments
            if stored is None and not has_cached_message:
                self._snapshots_missed += 1
            sent = await self._send_log(
                ref.guild_id,
                message_delete_embed(
                    effective_ref,
                    author_id=author_id,
                    author_name=author_name,
                    content=content,
                    attachments=attachments,
                ),
            )
            if sent or self._logging_channel(ref.guild_id) is None:
                await snapshots.delete(ref)

    async def _on_bulk_delete(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, MessageBulkDeleteEvent) or not payload.refs:
            return
        refs = payload.refs
        guild_id = refs[0].guild_id
        async with self._message_lock_for(guild_id):
            self._events_seen += 1
            _, snapshots = self._require_started()
            stored = await snapshots.get_many(refs)
            if len(stored) < len(refs):
                self._snapshots_missed += len(refs) - len(stored)
            if self._ignores_bots(guild_id):
                bot_message_ids = {
                    int(message_id) for message_id in getattr(payload, "bot_message_ids", ())
                }
                bot_message_ids.update(item.ref.message_id for item in stored if item.author_is_bot)
                bot_refs = tuple(ref for ref in refs if ref.message_id in bot_message_ids)
                if bot_refs:
                    await snapshots.delete_many(bot_refs)
                    refs = tuple(ref for ref in refs if ref.message_id not in bot_message_ids)
                    stored = [item for item in stored if item.ref.message_id not in bot_message_ids]
                if not refs:
                    self._report_health()
                    return
            effective_ref = stored[0].ref if stored else refs[0]
            should_log = self._tracks_message(effective_ref) and self._guild_values(guild_id).get(
                FIELD_LOG_BULK_DELETES, True
            )
            sent = (
                await self._send_log(guild_id, bulk_delete_embed(refs, stored))
                if should_log
                else True
            )
            if sent:
                await snapshots.delete_many(refs)
            self._report_health()

    async def _on_invite_create(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, InviteCreateEvent):
            return
        guild_id = payload.invite.guild_id
        values = self._guild_values(guild_id)
        should_log = self._logging_channel(guild_id) is not None and bool(
            values.get(FIELD_LOG_INVITE_CREATE, True)
        )
        should_track = self._retains_invite_state(guild_id)
        if not should_log and not should_track:
            return
        async with self._invite_lock_for(guild_id):
            self._events_seen += 1
            if should_track:
                self._invite_tracker.created(payload.invite)
            if should_log:
                await self._send_log(guild_id, invite_create_embed(payload.invite))
            else:
                self._report_health()

    async def _on_invite_delete(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, InviteDeleteEvent):
            return
        guild_id = payload.invite.guild_id
        values = self._guild_values(guild_id)
        should_log = self._logging_channel(guild_id) is not None and bool(
            values.get(FIELD_LOG_INVITE_DELETE, True)
        )
        should_track = self._retains_invite_state(guild_id)
        if not should_log and not should_track:
            return
        async with self._invite_lock_for(guild_id):
            self._events_seen += 1
            invite = (
                self._invite_tracker.deleted(payload.invite, now=self._clock())
                if should_track
                else payload.invite
            )
            if should_log:
                await self._send_log(guild_id, invite_delete_embed(invite))
            else:
                self._report_health()

    async def _on_member_join(self, event: Event) -> None:
        payload = event.payload
        if not isinstance(payload, MemberJoinEvent):
            return
        guild_id = payload.member.guild_id
        if not self._logs_member_joins(guild_id):
            return
        async with self._invite_lock_for(guild_id):
            self._events_seen += 1
            attribution = await self._attribute_join(guild_id)
            await self._send_log(
                guild_id,
                member_join_embed(
                    user_id=payload.member.user_id,
                    display_name=payload.member.display_name,
                    invite=attribution.invite,
                    attribution=attribution.detail,
                ),
            )

    async def _attribute_join(self, guild_id: int) -> InviteAttribution:
        ctx, _ = self._require_started()
        try:
            invites = await ctx.discord.fetch_invites(guild_id)
        except Exception:
            self._invite_tracker.invalidate(guild_id)
            log.warning("Could not fetch invites for guild %s", guild_id, exc_info=True)
            ctx.health.report(
                "degraded",
                "Invite counters are unavailable; verify Manage Server and Discord connectivity.",
                key=self._guild_health_key("invites", guild_id),
            )
            return InviteAttribution(
                None,
                "Invite counters unavailable (verify Manage Server and connectivity)",
            )
        ctx.health.report("healthy", key=self._guild_health_key("invites", guild_id))
        return self._invite_tracker.attribute(guild_id, invites, now=self._clock())

    async def _seed_invites(self) -> None:
        ctx, _ = self._require_started()
        if ctx.guild_settings is None:
            return
        for guild_id in ctx.guild_settings.guild_ids():
            async with self._invite_lock_for(guild_id):
                await self._sync_invite_tracking(guild_id)

    async def _prune_snapshots(self, _run: JobRun) -> None:
        _, snapshots = self._require_started()
        removed = await self._reconcile_snapshots()
        expired = await snapshots.prune(now=self._clock())
        removed += expired
        self._snapshots_pruned += expired
        log.info("Pruned %s expired Discord logging snapshot(s)", removed)
        self._report_health()

    async def _command_setup(self, interaction: ModuleInteraction) -> None:
        ctx, _ = self._require_started()
        if ctx.proposals is None or ctx.guild_settings is None:
            await interaction.respond("Configuration proposals are unavailable.", ephemeral=True)
            return
        guild_id = interaction.guild_id
        channel_id = int(interaction.options["channel"])
        channel_error = await self._logging_channel_error(guild_id, channel_id)
        if channel_error is not None:
            await interaction.respond(channel_error, ephemeral=True)
            return
        actor = ProposalActor(
            user_id=str(interaction.user_id),
            source=f"{MODULE_NAME}:setup",
            guild_id=str(guild_id),
            channel_id=str(interaction.channel_id),
        )
        target = f"guild:{guild_id}:{MODULE_NAME}"
        proposed = {**ctx.guild_settings.get(guild_id).values, FIELD_LOGGING_CHANNEL: channel_id}
        try:
            snapshot = await ctx.proposals.snapshot(target, actor=actor)
            proposal = await ctx.proposals.propose(
                target=target,
                content=render_guild_settings(proposed),
                summary=f"Send Discord audit logs to <#{channel_id}>",
                actor=actor,
                expected_revision=snapshot.revision,
            )
        except ProposalError as error:
            await interaction.respond(f"Could not propose the change: {error}", ephemeral=True)
            return
        await interaction.respond(
            f"Proposed (`{proposal.proposal_id}`). Staff can approve it from the review card.",
            ephemeral=True,
        )

    async def _store(self, message: MessageSnapshot) -> None:
        _, snapshots = self._require_started()
        await snapshots.put(message, expires_at=self._expiry(message.ref.guild_id))

    async def _send_log(self, guild_id: int, embed: OutgoingEmbed) -> bool:
        ctx, _ = self._require_started()
        channel_id = self._logging_channel(guild_id)
        if channel_id is None:
            return False
        health_key = self._guild_health_key("delivery", guild_id)
        channel_error = await self._logging_channel_error(guild_id, channel_id)
        if channel_error is not None:
            self._logs_failed += 1
            ctx.health.report(
                "degraded",
                f"Logging channel {channel_id} in guild {guild_id} is unavailable: {channel_error}",
                {"delivery_failures": float(self._logs_failed)},
                key=health_key,
            )
            log.warning(
                "Discord log destination rejected for guild %s: %s", guild_id, channel_error
            )
            return False
        try:
            await ctx.discord.send_message(channel_id, embed=embed)
        except Exception:
            self._logs_failed += 1
            ctx.health.report(
                "degraded",
                f"Could not post to logging channel {channel_id} in guild {guild_id}.",
                {"delivery_failures": float(self._logs_failed)},
                key=health_key,
            )
            log.exception("Could not deliver Discord log for guild %s", guild_id)
            return False
        self._logs_sent += 1
        ctx.health.report("healthy", key=health_key)
        self._report_health()
        return True

    async def _validate_logging_channels(self, guild_ids: Sequence[int] | None = None) -> None:
        ctx, _ = self._require_started()
        if ctx.guild_settings is None:
            return
        selected = tuple(guild_ids) if guild_ids is not None else ctx.guild_settings.guild_ids()
        for guild_id in selected:
            health_key = self._guild_health_key("delivery", guild_id)
            channel_id = self._logging_channel(guild_id)
            if channel_id is None:
                ctx.health.report("healthy", key=health_key)
                continue
            channel_error = await self._logging_channel_error(guild_id, channel_id)
            if channel_error is None:
                ctx.health.report("healthy", key=health_key)
                continue
            ctx.health.report(
                "degraded",
                f"Logging channel {channel_id} in guild {guild_id} is unavailable: {channel_error}",
                key=health_key,
            )

    async def _logging_channel_error(self, guild_id: int, channel_id: int) -> str | None:
        ctx, _ = self._require_started()
        try:
            channel = await ctx.discord.fetch_channel(guild_id, channel_id)
        except Exception:
            log.warning(
                "Could not inspect logging channel %s in guild %s",
                channel_id,
                guild_id,
                exc_info=True,
            )
            return "The logging channel could not be inspected. Try again later."
        if channel is None:
            return "Choose a text channel or active thread in this server."
        if channel.kind == "forum":
            return "Forum channels cannot receive audit logs; choose a text channel or thread."
        if channel.kind == "thread" and channel.archived:
            return "Archived threads cannot receive audit logs; choose an active channel."
        return None

    def _tracks_message(self, ref: MessageRef) -> bool:
        logging_channel = self._logging_channel(ref.guild_id)
        if logging_channel is None or not self._tracks_messages(ref.guild_id):
            return False
        locations = {ref.channel_id}
        if ref.parent_channel_id is not None:
            locations.add(ref.parent_channel_id)
        if logging_channel in locations:
            return False
        ignored = self._guild_values(ref.guild_id).get(FIELD_IGNORED_CHANNELS, ()) or ()
        return locations.isdisjoint(int(item) for item in ignored)

    def _logging_channel(self, guild_id: int) -> int | None:
        ctx = self._ctx
        if ctx is None or ctx.guild_settings is None or not ctx.guild_settings.is_enabled(guild_id):
            return None
        value = self._guild_values(guild_id).get(FIELD_LOGGING_CHANNEL)
        return int(value) if value else None

    def _logs_member_joins(self, guild_id: int) -> bool:
        return self._logging_channel(guild_id) is not None and bool(
            self._guild_values(guild_id).get(FIELD_LOG_MEMBER_JOINS, True)
        )

    def _retains_invite_state(self, guild_id: int) -> bool:
        if self._logging_channel(guild_id) is None:
            return False
        values = self._guild_values(guild_id)
        return bool(
            values.get(FIELD_LOG_INVITE_DELETE, True) or values.get(FIELD_LOG_MEMBER_JOINS, True)
        )

    def _tracks_messages(self, guild_id: int) -> bool:
        values = self._guild_values(guild_id)
        return any(
            bool(values.get(field, True))
            for field in (FIELD_LOG_EDITS, FIELD_LOG_DELETES, FIELD_LOG_BULK_DELETES)
        )

    def _ignores_bots(self, guild_id: int) -> bool:
        return bool(self._guild_values(guild_id).get(FIELD_IGNORE_BOTS, True))

    def _expiry(self, guild_id: int) -> float:
        days = int(self._guild_values(guild_id).get(FIELD_RETENTION_DAYS, 30))
        return self._clock() + min(max(days, 1), 365) * DAY_SECONDS

    def _guild_values(self, guild_id: int) -> Mapping[str, Any]:
        ctx = self._ctx
        if ctx is None or ctx.guild_settings is None:
            return {}
        return ctx.guild_settings.get(guild_id).values

    def _on_guild_change(self, guild_id: int) -> None:
        log.info("Discord logging settings changed for guild %s", guild_id)
        task = asyncio.create_task(
            self._reconcile_changed_guild(guild_id),
            name=f"{MODULE_NAME}:reconcile:{guild_id}",
        )
        self._maintenance_tasks.add(task)
        task.add_done_callback(self._maintenance_tasks.discard)

    async def _reconcile_changed_guild(self, guild_id: int) -> None:
        try:
            await self._reconcile_snapshots((guild_id,))
            await self._validate_logging_channels((guild_id,))
            async with self._invite_lock_for(guild_id):
                await self._sync_invite_tracking(guild_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not reconcile Discord logging settings for guild %s", guild_id)
            if self._ctx is not None:
                self._ctx.health.report(
                    "degraded",
                    f"Could not apply Discord logging settings for guild {guild_id}.",
                    key=self._guild_health_key("snapshot_maintenance", guild_id),
                )
            return
        if self._ctx is not None:
            self._ctx.health.report(
                "healthy", key=self._guild_health_key("snapshot_maintenance", guild_id)
            )
            self._report_health()

    async def _reconcile_snapshots(self, guild_ids: Sequence[int] | None = None) -> int:
        ctx, snapshots = self._require_started()
        if guild_ids is None:
            configured = ctx.guild_settings.guild_ids() if ctx.guild_settings is not None else ()
            guild_ids = tuple(sorted({*configured, *await snapshots.guild_ids()}))
        removed = 0
        for guild_id in dict.fromkeys(guild_ids):
            async with self._message_lock_for(guild_id):
                values = self._guild_values(guild_id)
                enabled = (
                    ctx.guild_settings is not None
                    and ctx.guild_settings.is_enabled(guild_id)
                    and self._logging_channel(guild_id) is not None
                    and self._tracks_messages(guild_id)
                )
                if not enabled:
                    removed += await snapshots.reconcile_guild(
                        guild_id,
                        now=self._clock(),
                        retention_seconds=None,
                    )
                    continue
                days = int(values.get(FIELD_RETENTION_DAYS, 30))
                ignored = values.get(FIELD_IGNORED_CHANNELS, ()) or ()
                excluded = {int(channel_id) for channel_id in ignored}
                logging_channel = self._logging_channel(guild_id)
                if logging_channel is not None:
                    excluded.add(logging_channel)
                removed += await snapshots.reconcile_guild(
                    guild_id,
                    now=self._clock(),
                    retention_seconds=min(max(days, 1), 365) * DAY_SECONDS,
                    excluded_channel_ids=tuple(sorted(excluded)),
                )
        self._snapshots_pruned += removed
        return removed

    async def _sync_invite_tracking(self, guild_id: int) -> None:
        ctx, _ = self._require_started()
        health_key = self._guild_health_key("invites", guild_id)
        if not self._retains_invite_state(guild_id):
            self._invite_tracker.invalidate(guild_id)
        if not self._logs_member_joins(guild_id):
            ctx.health.report("healthy", key=health_key)
            return
        if self._invite_tracker.has_baseline(guild_id):
            return
        try:
            invites = await ctx.discord.fetch_invites(guild_id)
        except Exception:
            self._invite_tracker.invalidate(guild_id)
            log.warning("Could not initialize invites for guild %s", guild_id, exc_info=True)
            ctx.health.report(
                "degraded",
                f"Invite counters are unavailable in guild {guild_id}; verify Manage Server "
                "and Discord connectivity.",
                key=health_key,
            )
            return
        self._invite_tracker.seed(guild_id, invites)
        ctx.health.report("healthy", key=health_key)

    def _message_lock_for(self, guild_id: int) -> asyncio.Lock:
        return self._message_locks.setdefault(guild_id, asyncio.Lock())

    def _invite_lock_for(self, guild_id: int) -> asyncio.Lock:
        return self._invite_locks.setdefault(guild_id, asyncio.Lock())

    @staticmethod
    def _guild_health_key(category: str, guild_id: int) -> str:
        return f"{category}:{guild_id}"

    def _report_health(self) -> None:
        if self._ctx is None:
            return
        self._ctx.health.report(
            "healthy",
            "",
            {
                "events_seen": float(self._events_seen),
                "logs_sent": float(self._logs_sent),
                "logs_failed": float(self._logs_failed),
                "snapshots_missed": float(self._snapshots_missed),
                "snapshots_pruned": float(self._snapshots_pruned),
            },
        )

    def _require_started(self) -> tuple[ModuleRuntimeContext, SnapshotStore]:
        if self._ctx is None or self._snapshots is None:
            raise RuntimeError(f"{MODULE_NAME} is not started")
        return self._ctx, self._snapshots


__all__ = [
    "COMMAND_GROUP",
    "MODULE_NAME",
    "PRUNE_HANDLER",
    "PRUNE_JOB_KEY",
    "DiscordLoggingModule",
]
