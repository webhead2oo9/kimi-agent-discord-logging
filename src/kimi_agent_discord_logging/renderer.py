"""Discord embed rendering with conservative platform limits."""

from __future__ import annotations

from collections.abc import Sequence

from kimi_agent_module_api import InviteSnapshot
from kimi_agent_module_api.contracts import AttachmentSnapshot, MessageRef, OutgoingEmbed

from kimi_agent_discord_logging.snapshots import StoredMessage

COLOR_EDIT = 0xF1C40F
COLOR_DELETE = 0xE74C3C
COLOR_INVITE = 0x3498DB
COLOR_JOIN = 0x2ECC71
_MARKDOWN_SPECIAL = frozenset("\\`*_~|>[]()")


def message_edit_embed(
    ref: MessageRef,
    *,
    author_id: int | None,
    author_name: str,
    before: str,
    after: str,
) -> OutgoingEmbed:
    return OutgoingEmbed(
        title="Message edited",
        description=_message_context(ref, author_id, author_name, include_jump=True),
        color=COLOR_EDIT,
        fields=(
            ("Before", _content(before), False),
            ("After", _content(after), False),
        ),
        footer=f"Message ID: {ref.message_id}",
        timestamp=True,
    )


def message_delete_embed(
    ref: MessageRef,
    *,
    author_id: int | None,
    author_name: str,
    content: str | None,
    attachments: Sequence[AttachmentSnapshot],
) -> OutgoingEmbed:
    fields: list[tuple[str, str, bool]] = []
    if content is not None:
        fields.append(("Content", _content(content), False))
    if attachments:
        fields.append(("Attachments", _attachments(attachments), False))
    if not fields:
        fields.append(
            ("Content", "*The message was not present in the bot's snapshot cache.*", False)
        )
    return OutgoingEmbed(
        title="Message deleted",
        description=_message_context(ref, author_id, author_name, include_jump=False),
        color=COLOR_DELETE,
        fields=tuple(fields),
        footer=f"Message ID: {ref.message_id}",
        timestamp=True,
    )


def bulk_delete_embed(refs: Sequence[MessageRef], stored: Sequence[StoredMessage]) -> OutgoingEmbed:
    samples: list[str] = []
    for item in stored[:8]:
        preview = " ".join(item.content.split()) or "(no text content)"
        samples.append(
            f"`{item.ref.message_id}` <@{item.author_id}>: {_clip(_escape_markdown(preview), 140)}"
        )
    description = f"Deleted **{len(refs)}** messages in <#{refs[0].channel_id}>."
    fields = (("Recovered snapshots", _clip("\n".join(samples), 1024), False),) if samples else ()
    return OutgoingEmbed(
        title="Messages bulk deleted",
        description=description,
        color=COLOR_DELETE,
        fields=fields,
        footer=f"Recovered {len(stored)} of {len(refs)} snapshots",
        timestamp=True,
    )


def invite_create_embed(invite: InviteSnapshot) -> OutgoingEmbed:
    creator = f"<@{invite.inviter_id}> (`{invite.inviter_id}`)" if invite.inviter_id else "Unknown"
    channel = f"<#{invite.channel_id}>" if invite.channel_id else "Unknown"
    return OutgoingEmbed(
        title="Invite created",
        description=f"Invite [`{invite.code}`](https://discord.gg/{invite.code}) was created.",
        color=COLOR_INVITE,
        fields=(("Created by", creator, True), ("Channel", channel, True)),
        footer=_invite_limits(invite),
        timestamp=True,
    )


def invite_delete_embed(invite: InviteSnapshot) -> OutgoingEmbed:
    creator = f"<@{invite.inviter_id}> (`{invite.inviter_id}`)" if invite.inviter_id else "Unknown"
    return OutgoingEmbed(
        title="Invite deleted",
        description=f"Invite `{invite.code}` is no longer available.",
        color=COLOR_INVITE,
        fields=(("Originally created by", creator, False),),
        footer=_invite_limits(invite),
        timestamp=True,
    )


def member_join_embed(
    *,
    user_id: int,
    display_name: str,
    invite: InviteSnapshot | None,
    attribution: str,
) -> OutgoingEmbed:
    member = (
        f"{_escape_markdown(display_name)} (<@{user_id}>, `{user_id}`)"
        if display_name
        else f"<@{user_id}> (`{user_id}`)"
    )
    if invite is None:
        invite_text = "Unknown (vanity URL, missing permission, or ambiguous invite counters)"
        inviter_text = "Unknown"
    else:
        invite_text = f"[`{invite.code}`](https://discord.gg/{invite.code})"
        uses = invite.uses
        if uses is not None:
            invite_text += f" · {uses} use{'s' if uses != 1 else ''}"
        inviter_text = (
            f"<@{invite.inviter_id}> (`{invite.inviter_id}`)" if invite.inviter_id else "Unknown"
        )
    return OutgoingEmbed(
        title="Member joined",
        description=member,
        color=COLOR_JOIN,
        fields=(("Invite", invite_text, False), ("Invite created by", inviter_text, False)),
        footer=attribution,
        timestamp=True,
    )


def _message_context(
    ref: MessageRef, author_id: int | None, author_name: str, *, include_jump: bool
) -> str:
    author = "Unknown author"
    if author_id is not None:
        author = f"<@{author_id}> (`{author_id}`)"
        if author_name:
            author = f"{_escape_markdown(author_name)} · {author}"
    context = f"{author}\nChannel: <#{ref.channel_id}>"
    if include_jump:
        context += (
            f" · [Jump to message](https://discord.com/channels/"
            f"{ref.guild_id}/{ref.channel_id}/{ref.message_id})"
        )
    return _clip(context, 4096)


def _content(value: str) -> str:
    text = value.strip()
    return _clip(text, 1024) if text else "*(no text content)*"


def _attachments(items: Sequence[AttachmentSnapshot]) -> str:
    lines = [
        f"[{_escape_markdown(item.filename)}]({item.url}) · {item.size} bytes"
        for item in items[:10]
    ]
    if len(items) > 10:
        lines.append(f"…and {len(items) - 10} more")
    return _clip("\n".join(lines), 1024)


def _invite_limits(invite: InviteSnapshot) -> str:
    uses = "unknown" if invite.uses is None else str(invite.uses)
    maximum = "unlimited" if not invite.max_uses else str(invite.max_uses)
    return f"Code: {invite.code} · Uses: {uses}/{maximum}"


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _escape_markdown(value: str) -> str:
    return "".join(
        f"\\{character}" if character in _MARKDOWN_SPECIAL else character for character in value
    )


__all__ = [
    "bulk_delete_embed",
    "invite_create_embed",
    "invite_delete_embed",
    "member_join_embed",
    "message_delete_embed",
    "message_edit_embed",
]
