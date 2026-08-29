"""Forward-only database migrations for message snapshots."""

from __future__ import annotations

from kimi_agent_module_api import ScopedModuleMigration
from kimi_agent_module_api.contracts import MigrationContext


async def _create_message_snapshots(ctx: MigrationContext) -> None:
    table = ctx.table("message_snapshots")
    await ctx.connection.execute(
        f"""
        CREATE TABLE {table} (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            author_display_name TEXT NOT NULL,
            author_is_bot INTEGER NOT NULL,
            content TEXT NOT NULL,
            attachments_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            edited_at REAL,
            expires_at REAL NOT NULL,
            PRIMARY KEY (guild_id, channel_id, message_id)
        )
        """
    )
    await ctx.connection.execute(
        f"CREATE INDEX {ctx.table('message_snapshots_expiry')} ON {table} (expires_at)"
    )


async def _add_parent_channel_to_message_snapshots(ctx: MigrationContext) -> None:
    table = ctx.table("message_snapshots")
    await ctx.connection.execute(f"ALTER TABLE {table} ADD COLUMN parent_channel_id INTEGER")
    # Older rows cannot be classified against a newly ignored thread parent.
    # This table is a disposable reconstruction cache, so purge ambiguous rows
    # once rather than retain content that new privacy settings cannot govern.
    await ctx.connection.execute(f"DELETE FROM {table}")
    await ctx.connection.execute(
        f"CREATE INDEX {ctx.table('message_snapshots_parent')} "
        f"ON {table} (guild_id, parent_channel_id)"
    )


MIGRATIONS: tuple[ScopedModuleMigration, ...] = (
    ("001_create_message_snapshots", _create_message_snapshots),
    ("002_add_parent_channel_to_message_snapshots", _add_parent_channel_to_message_snapshots),
)

__all__ = ["MIGRATIONS"]
