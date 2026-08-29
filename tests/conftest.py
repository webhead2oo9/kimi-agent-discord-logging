"""Standalone module test harness built entirely from SDK fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
from kimi_agent_module_api import InviteSnapshot, ModuleCapabilities, ModuleRuntimeContext
from kimi_agent_module_api.contracts import ChannelSnapshot
from kimi_agent_module_api.testing import (
    FakeDiscordActions,
    FakeEvents,
    FakeGuildSettings,
    FakeHealth,
    FakeHttp,
    FakeInteractions,
    FakeProposals,
    FakeScheduler,
    FakeServiceRegistry,
    FakeTrust,
    MemoryStorage,
)

from kimi_agent_discord_logging.guild_settings import FIELD_LOGGING_CHANNEL
from kimi_agent_discord_logging.module import MODULE_NAME, DiscordLoggingModule
from kimi_agent_discord_logging.spec import SPEC

GUILD = 100
LOG_CHANNEL = 900
SOURCE_CHANNEL = 200
AUTHOR = 300
INVITER = 400
MEMBER = 500
T0 = 1_000_000.0


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@dataclass(slots=True)
class Harness:
    module: DiscordLoggingModule
    ctx: ModuleRuntimeContext
    clock: Clock
    events: FakeEvents
    scheduler: FakeScheduler
    discord: FakeDiscordActions
    interactions: FakeInteractions
    guild_settings: FakeGuildSettings
    health: FakeHealth
    proposals: FakeProposals
    storage: MemoryStorage


@pytest_asyncio.fixture
async def storage() -> AsyncIterator[MemoryStorage]:
    async with MemoryStorage.open(MODULE_NAME) as memory:
        yield memory


@pytest_asyncio.fixture
async def started(storage: MemoryStorage, tmp_path: Path) -> AsyncIterator[Harness]:
    clock = Clock()
    module = DiscordLoggingModule(clock=clock)
    await storage.migrate(module.scoped_migrations)
    events = FakeEvents(MODULE_NAME)
    scheduler = FakeScheduler()
    discord = FakeDiscordActions(MODULE_NAME, SPEC.permissions.discord_actions)
    discord.channels[GUILD, LOG_CHANNEL] = ChannelSnapshot(GUILD, LOG_CHANNEL, "text", "audit-log")
    discord.invites[GUILD] = (
        InviteSnapshot(
            GUILD,
            "welcome",
            channel_id=SOURCE_CHANNEL,
            inviter_id=INVITER,
            uses=0,
            max_uses=0,
        ),
    )
    interactions = FakeInteractions(MODULE_NAME)
    guild_settings = FakeGuildSettings({GUILD: {FIELD_LOGGING_CHANNEL: LOG_CHANNEL}})
    health = FakeHealth()
    proposals = FakeProposals(MODULE_NAME)
    ctx = ModuleRuntimeContext(
        module_name=MODULE_NAME,
        is_guild_active=lambda _guild_id: True,
        current_config_dir=lambda: tmp_path,
        capabilities=ModuleCapabilities(
            frozenset(
                {
                    "proposals.v2",
                    "discord.members.v1",
                    "discord.message_content.v1",
                }
            ),
            members_intent=True,
            message_content_intent=True,
        ),
        events=events,
        scheduler=scheduler,
        storage=storage,
        health=health,
        discord=discord,
        interactions=interactions,
        http=FakeHttp(),
        services=FakeServiceRegistry(),
        trust=FakeTrust(),
        guild_settings=guild_settings,
        proposals=proposals,
    )
    await module.start(ctx)
    try:
        yield Harness(
            module,
            ctx,
            clock,
            events,
            scheduler,
            discord,
            interactions,
            guild_settings,
            health,
            proposals,
            storage,
        )
    finally:
        await module.close()
