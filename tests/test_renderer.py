"""Embed rendering remains readable and within Discord's field limits."""

from __future__ import annotations

from kimi_agent_module_api.contracts import AttachmentSnapshot, MessageRef

from kimi_agent_discord_logging.renderer import member_join_embed, message_delete_embed


def test_user_controlled_labels_cannot_inject_embed_markdown() -> None:
    embed = message_delete_embed(
        MessageRef(1, 2, 3),
        author_id=4,
        author_name="**Admin** [click]",
        content="unchanged message content",
        attachments=(
            AttachmentSnapshot(
                5,
                "[report](spoof).txt",
                "https://cdn.example/report.txt",
                10,
                "text/plain",
            ),
        ),
    )

    assert embed.description is not None
    assert "\\*\\*Admin\\*\\* \\[click\\]" in embed.description
    assert "[\\[report\\]\\(spoof\\).txt](https://cdn.example/report.txt)" in embed.fields[1][1]


def test_member_display_name_is_rendered_as_plain_text() -> None:
    embed = member_join_embed(
        user_id=4,
        display_name="_not italic_",
        invite=None,
        attribution="No invite use counter changed",
    )

    assert embed.description is not None
    assert embed.description.startswith("\\_not italic\\_")


def test_long_deleted_content_is_clipped_to_the_field_limit() -> None:
    embed = message_delete_embed(
        MessageRef(1, 2, 3),
        author_id=None,
        author_name="",
        content="x" * 2_000,
        attachments=(),
    )

    assert len(embed.fields[0][1]) == 1_024
    assert embed.fields[0][1].endswith("…")
