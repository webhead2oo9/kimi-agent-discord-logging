"""Per-guild logging configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from kimi_agent_module_api import GuildSettingsSchema
from kimi_agent_module_api.contracts import GuildSettingField

FIELD_LOGGING_CHANNEL = "logging_channel_id"
FIELD_LOG_EDITS = "log_edits"
FIELD_LOG_DELETES = "log_deletes"
FIELD_LOG_BULK_DELETES = "log_bulk_deletes"
FIELD_LOG_INVITE_CREATE = "log_invite_create"
FIELD_LOG_INVITE_DELETE = "log_invite_delete"
FIELD_LOG_MEMBER_JOINS = "log_member_joins"
FIELD_IGNORE_BOTS = "ignore_bots"
FIELD_IGNORED_CHANNELS = "ignored_channel_ids"
FIELD_RETENTION_DAYS = "snapshot_retention_days"


def _validate(values: Mapping[str, Any]) -> Sequence[str]:
    retention = int(values.get(FIELD_RETENTION_DAYS, 30))
    if not 1 <= retention <= 365:
        return ("snapshot_retention_days must be between 1 and 365",)
    return ()


GUILD_SETTINGS = GuildSettingsSchema(
    fields=(
        GuildSettingField(
            FIELD_LOGGING_CHANNEL,
            "id",
            help="Channel that receives audit logs. Unset disables logging for this guild.",
        ),
        GuildSettingField(FIELD_LOG_EDITS, "bool", default=True),
        GuildSettingField(FIELD_LOG_DELETES, "bool", default=True),
        GuildSettingField(FIELD_LOG_BULK_DELETES, "bool", default=True),
        GuildSettingField(FIELD_LOG_INVITE_CREATE, "bool", default=True),
        GuildSettingField(FIELD_LOG_INVITE_DELETE, "bool", default=True),
        GuildSettingField(FIELD_LOG_MEMBER_JOINS, "bool", default=True),
        GuildSettingField(
            FIELD_IGNORE_BOTS,
            "bool",
            default=True,
            help="Do not post edit or deletion logs for messages authored by bot users.",
        ),
        GuildSettingField(
            FIELD_IGNORED_CHANNELS,
            "id_list",
            default=(),
            help="Channels whose messages are never stored or logged, including child threads.",
        ),
        GuildSettingField(
            FIELD_RETENTION_DAYS,
            "int",
            default=30,
            help="Days to retain message snapshots used to reconstruct later edits/deletes.",
        ),
    ),
    invalid_policy="disable_module",
    validate=_validate,
)

__all__ = [
    "FIELD_IGNORED_CHANNELS",
    "FIELD_IGNORE_BOTS",
    "FIELD_LOGGING_CHANNEL",
    "FIELD_LOG_BULK_DELETES",
    "FIELD_LOG_DELETES",
    "FIELD_LOG_EDITS",
    "FIELD_LOG_INVITE_CREATE",
    "FIELD_LOG_INVITE_DELETE",
    "FIELD_LOG_MEMBER_JOINS",
    "FIELD_RETENTION_DAYS",
    "GUILD_SETTINGS",
]
