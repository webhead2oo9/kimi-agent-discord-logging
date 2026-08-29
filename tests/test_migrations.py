"""Forward migrations preserve privacy and upgrade existing installations."""

from __future__ import annotations

import pytest
from kimi_agent_module_api.testing import MemoryStorage

from kimi_agent_discord_logging.migrations import MIGRATIONS


@pytest.mark.asyncio
async def test_parent_channel_migration_purges_unclassifiable_old_snapshots(
    storage: MemoryStorage,
) -> None:
    await storage.migrate(MIGRATIONS[:1])
    table = storage.table("message_snapshots")
    async with storage.write_transaction() as connection:
        await connection.execute(
            f"""
            INSERT INTO {table} (
                guild_id, channel_id, message_id, author_id, author_display_name,
                author_is_bot, content, attachments_json, created_at, edited_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, 2, 3, 4, "Ada", 0, "old content", "[]", 5.0, None, 10.0),
        )

    # MemoryStorage deliberately has no host migration ledger, so apply only
    # the newly released migration when simulating an upgraded installation.
    await storage.migrate(MIGRATIONS[1:])

    cursor = await storage.connection.execute(f"SELECT COUNT(*) FROM {table}")
    assert (await cursor.fetchone())[0] == 0
    columns = await storage.connection.execute(f"PRAGMA table_info({table})")
    assert "parent_channel_id" in {str(row[1]) for row in await columns.fetchall()}
