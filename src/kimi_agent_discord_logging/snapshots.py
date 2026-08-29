"""Durable, short-lived message snapshots used by the logging module."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from kimi_agent_module_api.contracts import (
    AttachmentSnapshot,
    MessageRef,
    MessageSnapshot,
    ModuleStorage,
)

_SELECT_COLUMNS = (
    "guild_id, channel_id, message_id, parent_channel_id, author_id, "
    "author_display_name, author_is_bot, content, attachments_json, created_at, edited_at"
)


@dataclass(frozen=True, slots=True)
class StoredMessage:
    ref: MessageRef
    author_id: int
    author_display_name: str
    author_is_bot: bool
    content: str
    attachments: tuple[AttachmentSnapshot, ...]
    created_at: float
    edited_at: float | None


class SnapshotStore:
    """Persist reconstruction data exclusively through module-scoped storage."""

    def __init__(self, storage: ModuleStorage) -> None:
        self._storage = storage
        self._table = storage.table("message_snapshots")

    async def put(self, message: MessageSnapshot, *, expires_at: float) -> None:
        attachments = json.dumps(
            [asdict(attachment) for attachment in message.attachments],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        async with self._storage.write_transaction() as connection:
            await connection.execute(
                f"""
                INSERT INTO {self._table} (
                    guild_id, channel_id, message_id, parent_channel_id, author_id,
                    author_display_name, author_is_bot, content, attachments_json,
                    created_at, edited_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id, message_id) DO UPDATE SET
                    parent_channel_id = excluded.parent_channel_id,
                    author_id = excluded.author_id,
                    author_display_name = excluded.author_display_name,
                    author_is_bot = excluded.author_is_bot,
                    content = excluded.content,
                    attachments_json = excluded.attachments_json,
                    edited_at = excluded.edited_at,
                    expires_at = excluded.expires_at
                """,
                (
                    message.ref.guild_id,
                    message.ref.channel_id,
                    message.ref.message_id,
                    message.ref.parent_channel_id,
                    message.author_id,
                    message.author_display_name,
                    int(message.author_is_bot),
                    message.content,
                    attachments,
                    message.created_at,
                    message.edited_at,
                    expires_at,
                ),
            )

    async def update_after_edit(
        self,
        ref: MessageRef,
        content: str,
        *,
        attachments: tuple[AttachmentSnapshot, ...],
        edited_at: float | None,
        expires_at: float,
    ) -> None:
        attachments_json = json.dumps(
            [asdict(attachment) for attachment in attachments],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        async with self._storage.write_transaction() as connection:
            await connection.execute(
                f"UPDATE {self._table} SET content = ?, attachments_json = ?, "
                "edited_at = ?, expires_at = ? "
                "WHERE guild_id = ? AND channel_id = ? AND message_id = ?",
                (
                    content,
                    attachments_json,
                    edited_at,
                    expires_at,
                    ref.guild_id,
                    ref.channel_id,
                    ref.message_id,
                ),
            )

    async def get(self, ref: MessageRef) -> StoredMessage | None:
        cursor = await self._storage.connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM {self._table} "
            "WHERE guild_id = ? AND channel_id = ? AND message_id = ?",
            (ref.guild_id, ref.channel_id, ref.message_id),
        )
        row = await cursor.fetchone()
        return _row(row) if row is not None else None

    async def delete(self, ref: MessageRef) -> None:
        async with self._storage.write_transaction() as connection:
            await connection.execute(
                f"DELETE FROM {self._table} "
                "WHERE guild_id = ? AND channel_id = ? AND message_id = ?",
                (ref.guild_id, ref.channel_id, ref.message_id),
            )

    async def get_many(self, refs: tuple[MessageRef, ...]) -> list[StoredMessage]:
        if not refs:
            return []
        predicates = " OR ".join(
            "(guild_id = ? AND channel_id = ? AND message_id = ?)" for _ in refs
        )
        parameters = tuple(
            value for ref in refs for value in (ref.guild_id, ref.channel_id, ref.message_id)
        )
        cursor = await self._storage.connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM {self._table} WHERE {predicates}",
            parameters,
        )
        by_key: dict[tuple[int, int, int], StoredMessage] = {}
        for row in await cursor.fetchall():
            stored = _row(row)
            key = (stored.ref.guild_id, stored.ref.channel_id, stored.ref.message_id)
            by_key[key] = stored
        return [
            by_key[key]
            for ref in refs
            if (key := (ref.guild_id, ref.channel_id, ref.message_id)) in by_key
        ]

    async def delete_many(self, refs: tuple[MessageRef, ...]) -> None:
        async with self._storage.write_transaction() as connection:
            await connection.executemany(
                f"DELETE FROM {self._table} "
                "WHERE guild_id = ? AND channel_id = ? AND message_id = ?",
                [(ref.guild_id, ref.channel_id, ref.message_id) for ref in refs],
            )

    async def prune(self, *, now: float) -> int:
        async with self._storage.write_transaction() as connection:
            cursor = await connection.execute(
                f"DELETE FROM {self._table} WHERE expires_at <= ?", (now,)
            )
            return max(0, int(cursor.rowcount or 0))

    async def guild_ids(self) -> tuple[int, ...]:
        cursor = await self._storage.connection.execute(
            f"SELECT DISTINCT guild_id FROM {self._table} ORDER BY guild_id"
        )
        return tuple(int(row[0]) for row in await cursor.fetchall())

    async def reconcile_guild(
        self,
        guild_id: int,
        *,
        now: float,
        retention_seconds: float | None,
        excluded_channel_ids: tuple[int, ...] = (),
    ) -> int:
        """Apply current privacy settings to snapshots already at rest.

        ``None`` retention means snapshotting is disabled for the guild and all
        existing rows should be removed. Otherwise expiry is recalculated from
        the latest stored message activity, so lowering a guild's retention
        takes effect on old rows as well as new events.
        """
        async with self._storage.write_transaction() as connection:
            if retention_seconds is None:
                cursor = await connection.execute(
                    f"DELETE FROM {self._table} WHERE guild_id = ?", (guild_id,)
                )
                return max(0, int(cursor.rowcount or 0))

            removed = 0
            if excluded_channel_ids:
                placeholders = ", ".join("?" for _ in excluded_channel_ids)
                cursor = await connection.execute(
                    f"DELETE FROM {self._table} WHERE guild_id = ? "
                    f"AND (channel_id IN ({placeholders}) "
                    f"OR parent_channel_id IN ({placeholders}))",
                    (guild_id, *excluded_channel_ids, *excluded_channel_ids),
                )
                removed += max(0, int(cursor.rowcount or 0))

            # Never revive a row that already passed the retention deadline
            # that applied when it was stored, even if retention was increased
            # before the daily prune job happened to delete it.
            cursor = await connection.execute(
                f"DELETE FROM {self._table} WHERE guild_id = ? AND expires_at <= ?",
                (guild_id, now),
            )
            removed += max(0, int(cursor.rowcount or 0))
            await connection.execute(
                f"UPDATE {self._table} "
                "SET expires_at = COALESCE(edited_at, created_at) + ? "
                "WHERE guild_id = ?",
                (retention_seconds, guild_id),
            )
            cursor = await connection.execute(
                f"DELETE FROM {self._table} WHERE guild_id = ? AND expires_at <= ?",
                (guild_id, now),
            )
            removed += max(0, int(cursor.rowcount or 0))
            return removed


def _row(row: Any) -> StoredMessage:
    raw_attachments = json.loads(str(row[8]))
    attachments = tuple(
        AttachmentSnapshot(
            attachment_id=int(item["attachment_id"]),
            filename=str(item["filename"]),
            url=str(item["url"]),
            size=int(item["size"]),
            content_type=(str(item["content_type"]) if item.get("content_type") else None),
        )
        for item in raw_attachments
    )
    return StoredMessage(
        ref=MessageRef(
            int(row[0]),
            int(row[1]),
            int(row[2]),
            int(row[3]) if row[3] is not None else None,
        ),
        author_id=int(row[4]),
        author_display_name=str(row[5]),
        author_is_bot=bool(row[6]),
        content=str(row[7]),
        attachments=attachments,
        created_at=float(row[9]),
        edited_at=float(row[10]) if row[10] is not None else None,
    )


__all__ = ["SnapshotStore", "StoredMessage"]
