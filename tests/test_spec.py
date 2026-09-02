"""The package declarations satisfy the same preflight rules as the host."""

from __future__ import annotations

from importlib.metadata import version

from kimi_agent_module_api import MODULE_API_VERSION
from kimi_agent_module_api.contracts import (
    validate_guild_settings_schema,
    validate_module_name,
    validate_permissions,
    validate_services,
    validate_subscription,
)
from kimi_agent_module_api.testing import load_context

from kimi_agent_discord_logging.guild_settings import FIELD_IGNORE_BOTS, FIELD_RETENTION_DAYS
from kimi_agent_discord_logging.migrations import MIGRATIONS
from kimi_agent_discord_logging.module import MODULE_NAME, DiscordLoggingModule
from kimi_agent_discord_logging.spec import SPEC, VERSION, create


def test_declarations_pass_host_preflight() -> None:
    assert SPEC.name == MODULE_NAME
    assert SPEC.api_version == MODULE_API_VERSION
    validate_module_name(SPEC.name)
    validate_permissions(SPEC.name, SPEC.permissions)
    validate_services(SPEC.name, SPEC.dependencies, SPEC.provides, SPEC.consumes)
    assert SPEC.guild_settings is not None
    validate_guild_settings_schema(SPEC.name, SPEC.guild_settings)
    for topic in SPEC.permissions.event_topics:
        validate_subscription(SPEC.name, SPEC.permissions, topic)
    assert SPEC.activation_capabilities == (
        "discord.members.v1",
        "discord.message_content.v1",
    )
    assert "fetch_channel" in SPEC.permissions.discord_actions


def test_distribution_and_spec_versions_match() -> None:
    assert version("kimi-agent-discord-logging") == VERSION == SPEC.version


def test_migration_ledger_is_append_only() -> None:
    assert tuple(name for name, _migration in MIGRATIONS) == (
        "001_create_message_snapshots",
        "002_add_parent_channel_to_message_snapshots",
    )


def test_create_registers_no_llm_tools() -> None:
    context, recorder = load_context(None)

    module = create(context)

    assert isinstance(module, DiscordLoggingModule)
    assert recorder.registry.tools == {}
    assert recorder.labels == {}


def test_retention_is_bounded() -> None:
    assert SPEC.guild_settings is not None and SPEC.guild_settings.validate is not None
    validate = SPEC.guild_settings.validate
    assert validate({FIELD_RETENTION_DAYS: 1}) == ()
    assert validate({FIELD_RETENTION_DAYS: 365}) == ()
    assert validate({FIELD_RETENTION_DAYS: 0})
    assert validate({FIELD_RETENTION_DAYS: 366})


def test_bot_messages_are_ignored_by_default() -> None:
    assert SPEC.guild_settings is not None
    fields = {field.name: field for field in SPEC.guild_settings.fields}

    assert fields[FIELD_IGNORE_BOTS].default is True
