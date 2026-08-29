"""Module identity, declarations, and load-time construction."""

from __future__ import annotations

from kimi_agent_module_api import AppModule, ModuleLoadContext, ModulePermissions, ModuleSpec
from kimi_agent_module_api.events import (
    TOPIC_INVITE_CREATE,
    TOPIC_INVITE_DELETE,
    TOPIC_MEMBER_JOIN,
    TOPIC_MESSAGE,
    TOPIC_MESSAGE_BULK_DELETE,
    TOPIC_MESSAGE_DELETE,
    TOPIC_MESSAGE_EDIT,
)

from kimi_agent_discord_logging.guild_settings import GUILD_SETTINGS
from kimi_agent_discord_logging.module import MODULE_NAME, DiscordLoggingModule

VERSION = "0.2.0"


def create(_ctx: ModuleLoadContext) -> AppModule:
    return DiscordLoggingModule()


SPEC = ModuleSpec(
    name=MODULE_NAME,
    version=VERSION,
    create=create,
    activation_capabilities=("discord.members.v1", "discord.message_content.v1"),
    permissions=ModulePermissions(
        discord_actions=frozenset(
            {"send_message", "fetch_message", "fetch_channel", "fetch_invites"}
        ),
        event_topics=(
            TOPIC_MESSAGE,
            TOPIC_MESSAGE_EDIT,
            TOPIC_MESSAGE_DELETE,
            TOPIC_MESSAGE_BULK_DELETE,
            TOPIC_INVITE_CREATE,
            TOPIC_INVITE_DELETE,
            TOPIC_MEMBER_JOIN,
        ),
    ),
    guild_settings=GUILD_SETTINGS,
)

__all__ = ["SPEC", "VERSION", "create"]
