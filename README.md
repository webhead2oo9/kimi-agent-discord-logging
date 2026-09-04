# Discord logging module for Kimi

This module adds an audit log to a Kimi Discord bot. It keeps a temporary copy of
recent messages so it can show what changed when a message is edited or deleted.
It can also log invite changes and make a careful guess about which invite a new
member used.

It is also a complete, working example of a Kimi module. It uses only the public
[`kimi-agent-module-api`](https://pypi.org/project/kimi-agent-module-api/), keeps
its own settings and database table, and its tests run without the rest of Kimi.

## What gets logged

| Event | What the log shows | Good to know |
|---|---|---|
| Message edit | Author, channel, before and after text, jump link | The old text is only available if the module saw the message earlier or Discord still had it cached. |
| Message delete | Author, channel, recovered content, attachment links | Discord does not say who deleted it. Content may be missing if the bot never saw the message. |
| Bulk delete | Count and up to eight recovered previews | Only messages the module saw earlier can be recovered. |
| Invite created or deleted | Code, channel, creator, and limits when Discord provides them | Discord only sends these events where the bot has **Manage Channels**. |
| Member join | Member and the likely invite and inviter | Discord does not say which invite was used, so this can be unknown. |

The module gives the AI no tools and never sends message content to a model.

## Before you install

You need:

- A Kimi installation that supports module API version 2, on Python 3.14 or newer.
- The bot's **Server Members Intent** and **Message Content Intent**, turned on
  both in Kimi's environment file and in the Discord Developer Portal.
- Permission to install a Python package into the environment that runs Kimi.
- A logging channel in each server that should use the module.

## Install

### 1. Install the package

Clone this repository next to Kimi and check out the tag or commit you want:

```console
git clone https://github.com/webhead2oo9/kimi-agent-discord-logging.git /path/to/kimi-agent-discord-logging
git -C /path/to/kimi-agent-discord-logging checkout --detach <tag-or-commit>
```

From Kimi's `bot/` directory, install it into Kimi's own Python environment. The
`--no-deps` flag keeps Kimi's already-installed dependencies in charge:

```console
uv pip install --python .venv/bin/python --no-deps --editable /path/to/kimi-agent-discord-logging
```

You can build and install a wheel instead:

```console
uv build --no-sources
cd /path/to/kimi-agent/bot
uv pip install --python .venv/bin/python --no-deps /path/to/kimi-agent-discord-logging/dist/kimi_agent_discord_logging-<version>-py3-none-any.whl
```

If you later run `uv sync` in the Kimi checkout, it may uninstall this module. Run
the install command again if that happens.

### 2. Turn the module on

Kimi does not load every module it finds. Add `discord_logging` to the
`KIMI_MODULES` line in Kimi's environment file, keeping anything already there:

```dotenv
KIMI_MODULES=moderation,discord_logging
```

### 3. Enable the intents

In the same environment file:

```dotenv
MEMBERS_INTENT=true
MESSAGE_CONTENT_INTENT=true
```

Then enable **Server Members Intent** and **Message Content Intent** in the Discord
Developer Portal under **Bot → Privileged Gateway Intents**. If either is missing,
Kimi leaves the module off and explains why in `/modules status`.

### 4. Give the bot permissions

In the logging channel: **View Channel**, **Send Messages**, and **Embed Links**.

In every channel you want logged: **View Channel**. **Read Message History** is
strongly recommended. Without it, the module still logs text changes after an
edit but forgets the old attachment list, so a removed attachment cannot show up
later in a deletion log by mistake.

For invite logging: **Manage Channels** in the channels where invites are made, so
Discord sends the invite events, and **Manage Server** so the bot can read invite
use counts. Without **Manage Server**, join logs still appear but say the invite
is unknown.

### 5. Configure each server

Create one settings file per server:

```text
<CONFIG_DIR>/guild-modules/<guild_id>/discord_logging.md
```

The file holds only this block. Do not add notes after the closing `---`. If a
value is invalid, Kimi turns the module off for that server and reports the
error.

```markdown
---
logging_channel_id: 123456789012345678
log_edits: true
log_deletes: true
log_bulk_deletes: true
log_invite_create: true
log_invite_delete: true
log_member_joins: true
ignore_bots: true
ignored_channel_ids: []
snapshot_retention_days: 30
---
```

| Field | Default | Meaning |
|---|---:|---|
| `logging_channel_id` | unset | Text channel or active thread that receives logs. When unset, the module stores and logs nothing for this server. |
| `log_edits` | `true` | Post edit logs. |
| `log_deletes` | `true` | Post single-delete logs. |
| `log_bulk_deletes` | `true` | Post bulk-delete summaries. |
| `log_invite_create` | `true` | Post invite-create logs. |
| `log_invite_delete` | `true` | Post invite-delete logs. |
| `log_member_joins` | `true` | Post join logs with invite attribution. |
| `ignore_bots` | `true` | Skip edit and delete logs for messages written by bots. |
| `ignored_channel_ids` | `[]` | Channels that are never stored or logged. Ignoring a channel also covers its threads. |
| `snapshot_retention_days` | `30` | How long temporary message copies are kept, from 1 to 365. |

The `log_*` switches only decide what gets posted. As long as a logging channel is
set, the module remembers eligible messages from every non-ignored channel so it
can handle a later edit or deletion. Put sensitive channels in
`ignored_channel_ids`. Changing the ignored list or retention period also cleans
up messages the module already holds. Clearing `logging_channel_id` removes that
server's saved messages and invite data.

Staff can also run `/logging setup #channel`. This creates a Kimi review card
rather than editing the file directly, and takes effect only after an authorized
operator approves it. The other settings still live in the file.

### 6. Restart and check

Restart Kimi. If it runs as the standard user service:

```console
systemctl --user restart kimi-agent.service
journalctl --user -u kimi-agent.service -n 100 --no-pager
```

Then confirm:

1. The startup log contains `Kimi module started: discord_logging <version>` and a
   successful command sync.
2. `/modules status` shows `discord_logging` as healthy in the server.
3. `/logging setup` is available to staff.
4. Editing and deleting a test message in a logged channel produces the expected
   embeds.
5. If invite logging is on, creating a throwaway invite produces a log.

## Invite attribution is a best guess

Discord tells the bot that someone joined, but not which invite they used. The
module records each invite's use count at startup and compares again after a
join. If exactly one count went up, it usually knows the invite. It also watches a
15-second window for one-use invites, which vanish as soon as they are used.

Vanity URLs, missing permissions, several people joining at once, and events
arriving out of order can all make the answer uncertain. The module then reports
the invite as unknown rather than guessing. Treat attribution as a clue, not as
proof for a moderation decision.

Each server has its own invite state, held in memory. Turning on join logging
starts fresh. Turning the module off or clearing its logging channel discards it.

## Data and privacy

The key point: this module reads ordinary messages in every channel the bot can
see, even when nobody mentions the bot. That is the only way it can show the
original message after an edit or deletion. Your server's privacy notice should
say so. Give the bot access only to channels you intend to log, and pick the
shortest retention period that works for your community.

It does not store messages from the logging channel or from ignored channels.
Bot-authored messages are stored for the same period even with `ignore_bots` on,
because Discord's delete events do not identify bot authors. The stored copy is
what lets the module skip those logs.

### What is stored

One table, `discord_logging_message_snapshots` in Kimi's shared database, with a
row per remembered message:

| Stored value | Why |
|---|---|
| Server, channel, parent channel, and message IDs | Find the message, honor ignored parents, and show where it came from. |
| Author ID, display name, and bot flag | Show who wrote it. |
| Message content | Recover edits and deletions Discord no longer has. |
| Attachment ID, filename, Discord URL, size, and content type | Link attachments in deletion logs. Attachment files are never downloaded. |
| Creation and edit timestamps | Preserve context. |
| Expiration timestamp | Enforce the retention setting. |

After a deletion log is posted, the stored copy is removed immediately. If posting
fails, the copy stays until it expires. Failed posts are not retried. Kimi runs a
daily job that removes expired rows.

After an edit, the module fetches the current message from Discord so the stored
attachment list stays accurate. If it cannot, it keeps the new text and clears the
old attachment list.

Invite codes, counters, and inviter IDs live only in memory and are gone when Kimi
stops. Log embeds posted to Discord follow that channel's own retention. Cleaning
the local table does not delete them. Outside Discord itself, the module sends no
data to any model, analytics service, or third party.

### Upgrading

Migration `002_add_parent_channel_to_message_snapshots` adds the parent channel to
stored rows. It clears the existing cache once, because older rows lack the
information needed to apply thread exclusions safely. New messages refill it.

### Uninstalling cleanly

Removing the module from `KIMI_MODULES` or uninstalling the package stops it, so it
can no longer clean up its table. Existing rows stay until the module runs again
or an operator drops the table. Backups keep copies according to your own policy.

To uninstall cleanly, first remove `logging_channel_id` from every server's
settings file while the module is still running. Wait for Kimi to notice, or
restart it once, so the stored messages are deleted. Then remove `discord_logging`
from `KIMI_MODULES` and uninstall the package.

Kimi modules run as Python inside the Kimi process and are not sandboxed. Review a
module's code before enabling it.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Startup says the entry point is missing | The package must be installed into the interpreter that runs the service. From `bot/`, run `.venv/bin/python -c "import importlib.metadata as m; print([e.name for e in m.entry_points(group='kimi_agent.modules')])"`. |
| Module shows as soft-disabled | Enable both intents in the environment file and the Developer Portal, then restart. `/modules status` names what is missing. |
| Module is healthy but nothing is posted | Check the server file has `logging_channel_id`, the channel is not ignored, the matching `log_*` field is true, `ignore_bots` is not excluding the author, and the bot can view, send, and embed there. |
| Delivery health is degraded, or Discord returns `403 Missing Access` | Recheck the bot's role and channel permissions for the logging channel. Health is tracked per server. |
| No invite create or delete logs | Grant **Manage Channels** where invites are made. |
| Join logs say the invite is unknown | Grant **Manage Server**, then restart so starting counts are recorded. Vanity URLs and simultaneous joins can still be unknown. |
| An edit or delete log says the snapshot was missing | The message predates startup, was in an ignored channel, expired, was not visible to the bot, or arrived while Message Content Intent was off. |
| `/logging setup` is absent | Confirm the module started and command sync succeeded. It is staff-only and server-only. |
| The module disappeared after `uv sync` | Reinstall it. |

## For developers

The module imports only Kimi's public module API. Tests use the fakes in
`kimi_agent_module_api.testing`, so you can work here without the full bot. Kimi's
[`docs/modules.md`](https://github.com/webhead2oo9/kimi-agent/blob/main/docs/modules.md)
covers the module lifecycle and API.

### Code map

| File | What it does |
|---|---|
| `pyproject.toml` | Package metadata, dependencies, and the `kimi_agent.modules` entry point. |
| `.github/workflows/ci.yml` | Formatting, lint, type checks, tests, dependency audit, and a package build. |
| `MANIFEST.in` | Extra files for the source package, including tests. |
| `src/kimi_agent_discord_logging/spec.py` | Describes the module to Kimi and lists the events and actions it needs. |
| `src/kimi_agent_discord_logging/guild_settings.py` | Defines and validates per-server settings. |
| `src/kimi_agent_discord_logging/migrations.py` | Creates and upgrades the database table. |
| `src/kimi_agent_discord_logging/snapshots.py` | Reads and writes stored message copies through `ModuleStorage`. |
| `src/kimi_agent_discord_logging/invites.py` | Tracks invite counters and refuses to guess when unclear. |
| `src/kimi_agent_discord_logging/renderer.py` | Builds embeds within Discord's length limits. |
| `src/kimi_agent_discord_logging/module.py` | Start, stop, event handling, log delivery, and health. |
| `tests/conftest.py` | Starts the module with the SDK's fake Discord and in-memory database. |
| `tests/test_spec.py` | Kimi can load the module and its declarations are valid. |
| `tests/test_module.py` | Main logging behavior through the public API. |
| `tests/test_invites.py` | Uncertain and out-of-order invite cases. |
| `tests/test_migrations.py` | Upgrades from an existing installation. |
| `tests/test_renderer.py` | Embed limits and user-provided names and filenames. |
| `tests/test_storage_lifecycle.py` | Retention, ignored channels, and cleanup when disabled. |

At startup Kimi loads `SPEC`, checks the declarations, creates the module, runs
migrations, and calls `start()`. At shutdown it calls `close()`, which unregisters
handlers and commands. Daily cleanup uses Kimi's scheduler, so it survives
restarts. Each server has its own message and invite locks, so events in one
server stay ordered without blocking others.

The module declares only what it uses: capabilities `discord.members.v1` and
`discord.message_content.v1`; Discord actions `send_message`, `fetch_message`,
`fetch_channel`, and `fetch_invites`; seven `discord.*` event topics; and no
direct bot or database access, outbound hosts, AI tools, or other modules.

### Local development

```console
uv sync --extra dev
uv --preview-features audit-command audit --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
uv build --no-sources
uv run twine check dist/*
```

The lock file installs `kimi-agent-module-api` from PyPI on purpose, to catch
accidental imports from a nearby Kimi checkout. To test an unreleased API change,
use a temporary local override and do not commit it.

When changing the module:

1. Keep the version in `pyproject.toml` and `spec.VERSION` equal.
2. Never rename, reorder, or edit a released migration. Add a new one for each
   database change. Kimi rejects changed migration history.
3. Declare any new event, action, host, or service before using it, and extend
   `test_declarations_pass_host_preflight`.
4. Update the settings table and privacy section whenever stored or observed data
   changes.
5. Run the checks, build the package, and try it in a sandbox Kimi with a real
   Discord server.

## License and name

MIT. See [LICENSE](LICENSE).

This is an independent community module for the Kimi Discord bot. It is not
affiliated with, endorsed by, or sponsored by Moonshot AI or its Kimi products.
Here, "Kimi" means the Discord bot that loads the module.
