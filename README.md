# Discord logging module for Kimi

This module adds a practical audit log to a Kimi Discord bot. It remembers
recent messages long enough to show what changed when a message is edited or
deleted. It can also log invite changes and make a careful guess about which
invite a new member used.

If you are building a Kimi module of your own, this repository is also a
complete working example. It uses only the public
[`kimi-agent-module-api`](https://pypi.org/project/kimi-agent-module-api/),
keeps its own settings and database table, and can be tested without importing
the rest of Kimi.

## What gets logged

| Event | What the log shows | What to know |
|---|---|---|
| Message edit | Author, channel, before/after text, and a jump link | The original text is available only if Discord cached it or the module saw the message earlier. |
| Message delete | Author, channel, recovered content, and attachment links | Discord does not say who deleted the message. Content may be unavailable if the bot never saw it. |
| Bulk delete | Count and up to eight recovered message previews | The module can recover only messages it saw earlier. |
| Invite create/delete | Code, channel, creator, and limits when Discord supplies them | Discord emits these events only where the bot has **Manage Channels**. |
| Member join | Member and likely invite/inviter | Discord does not provide the invite directly, so this can sometimes be unknown. |

The module does not give the LLM a tool, and it never sends message content to
a model.

## Requirements

- A Kimi installation that supports module API version 1.
- Python 3.14 or later, matching Kimi's runtime.
- The bot's **Server Members Intent** and **Message Content Intent**, enabled
  both in Kimi's configuration and in the Discord Developer Portal.
- Permission to install a Python package into the environment that runs Kimi.
- A logging channel in each server where the module should be active.

The package installs `kimi-agent-module-api>=1.0.0,<2` from PyPI. It does not
import code from a local Kimi checkout.

## Install and activate

There are two parts to installation: install the Python package, then tell
Kimi to load its `discord_logging` entry point. Kimi does not automatically
load every module it finds in the environment.

### 1. Install the package

For a normal installation, install the package from PyPI into the same virtual
environment that runs Kimi. Run this from Kimi's `bot/` directory:

```console
uv pip install --python .venv/bin/python kimi-agent-discord-logging==0.2.0
```

If you are working on the module, clone this repository next to the Kimi
checkout and install it in editable mode. Replace the example path:

```console
uv pip install --python .venv/bin/python --editable /path/to/kimi-agent-discord-logging
```

You can also build and install the wheel yourself:

```console
uv build --no-sources
cd /path/to/kimi-agent/bot
uv pip install --python .venv/bin/python /path/to/kimi-agent-discord-logging/dist/kimi_agent_discord_logging-0.2.0-py3-none-any.whl
```

If a later Kimi `uv sync` removes the module because it is not in Kimi's lock
file, run the install command again.

### 2. Activate the entry point

Add the module's entry-point name to the comma-separated `KIMI_MODULES` value
in the dotenv used by the running service. Keep any modules already listed:

```dotenv
KIMI_MODULES=discord_logging
```

For example, an existing `KIMI_MODULES=moderation` becomes
`KIMI_MODULES=moderation,discord_logging`.

### 3. Enable the required intents

Set both values in the same Kimi dotenv:

```dotenv
MEMBERS_INTENT=true
MESSAGE_CONTENT_INTENT=true
```

Then enable **Server Members Intent** and **Message Content Intent** for the bot
under **Discord Developer Portal → Bot → Privileged Gateway Intents**. If
either one is missing, Kimi leaves this module off and explains why in
`/modules status`.

### 4. Grant Discord permissions

In the logging channel, the bot needs:

- **View Channel**
- **Send Messages**
- **Embed Links**

In every channel you want logged, the bot needs **View Channel**.
**Read Message History** is strongly recommended so it can refresh the full
message after an edit. If it cannot do that, it still logs the text change but
forgets the old attachment list. This prevents a removed attachment from
showing up later in a deletion log.

For invite features, it also needs:

- **Manage Channels** in relevant channels so Discord sends invite-create and
  invite-delete events.
- **Manage Server** (`Manage Guild` in the API) so the bot can read current
  invite-use counters and attribute joins.

Missing **Manage Server** does not stop member-join logs; those logs report the
invite as unknown.

### 5. Configure each server

Create one settings file for each Discord server that should use the module:

```text
<CONFIG_DIR>/guild-modules/<guild_id>/discord_logging.md
```

Put this YAML block at the top of the file:

```markdown
---
logging_channel_id: 123456789012345678
log_edits: true
log_deletes: true
log_bulk_deletes: true
log_invite_create: true
log_invite_delete: true
log_member_joins: true
ignored_channel_ids: []
snapshot_retention_days: 30
---
```

You can add Markdown notes below the closing `---`. If the settings are
invalid, Kimi disables the module for that server and reports the error instead
of guessing what you meant.

| Field | Type | Default | Meaning |
|---|---|---:|---|
| `logging_channel_id` | Discord channel ID | unset | Text channel or active thread that receives logs. When unset, logging and temporary message storage are disabled for the server. |
| `log_edits` | boolean | `true` | Post edit logs. |
| `log_deletes` | boolean | `true` | Post single-delete logs. |
| `log_bulk_deletes` | boolean | `true` | Post bulk-delete summaries. |
| `log_invite_create` | boolean | `true` | Post invite-create logs. |
| `log_invite_delete` | boolean | `true` | Post invite-delete logs. |
| `log_member_joins` | boolean | `true` | Post member-join logs and invite attribution. |
| `ignored_channel_ids` | list of channel IDs | `[]` | Channels whose messages are neither stored nor logged. Ignoring a parent also covers its threads. |
| `snapshot_retention_days` | integer, 1–365 | `30` | How long temporary message copies are kept. |

The `log_edits`, `log_deletes`, and `log_bulk_deletes` switches decide which
events are posted. They do not decide which messages are temporarily stored.
As long as a logging channel is configured, the module remembers messages from
every non-ignored channel so it can handle a later edit or deletion.

Put sensitive channels in `ignored_channel_ids`. Their messages, including
messages in child threads, will not be stored or logged. When you change the
ignored list or retention period, the module also applies the new setting to
data it already holds. Clearing `logging_channel_id` removes that server's
saved messages and in-memory invite data.

Staff can also run `/logging setup #channel`. This creates a Kimi review card
instead of changing the file behind the scenes. The new channel takes effect
only after an authorized operator approves it. The other settings still live
in the file.

### 6. Restart and verify

Restart Kimi with the service manager you use. If it runs as the standard user
service:

```console
systemctl --user restart kimi-agent.service
systemctl --user status kimi-agent.service
journalctl --user -u kimi-agent.service -n 100 --no-pager
```

Use the actual unit name if your service was renamed. Then check the following:

1. Startup logs contain `Kimi module started: discord_logging 0.2.0` and a
   successful Discord command sync.
2. `/modules status` reports `discord_logging` as healthy in the target server.
3. `/logging setup` is registered for staff.
4. Editing and deleting a test message in a non-ignored channel produces the
   expected embeds.
5. Creating a disposable invite produces a log if invite logging is enabled.

## Invite attribution is best-effort

Discord tells the bot that someone joined, but not which invite they used. To
work around that, the module records each invite's use count at startup and
checks the counts again after a member joins. If exactly one count went up, it
can usually identify the invite. It also keeps a short 15-second window for
one-use invites, which disappear as soon as they are used.

Sometimes there is no safe answer. Vanity URLs, missing permissions, several
people joining at once, and invite changes arriving out of order can all make
the result uncertain. In those cases the module says the invite is unknown
instead of guessing. Treat invite attribution as a helpful clue, not proof for
a moderation decision.

Each server has its own invite state. Turning join logging on creates a fresh
starting point; turning the module off or clearing its logging channel removes
that in-memory data.

## Data and privacy

The important privacy detail is that this module sees ordinary messages in the
channels the bot can access, even when nobody mentions the bot. It needs to see
the original message if it is going to show that message after an edit or
deletion. It does not store messages from the logging channel or from channels
listed in `ignored_channel_ids`.

Your public privacy notice should explain this. Give the bot access only to
the channels you intend to log, and choose the shortest retention period that
works for your community.

### What is stored in the database

The module uses one table named `message_snapshots`. In Kimi's shared database,
its full name is `discord_logging_message_snapshots`. There is one row for each
message the module is temporarily remembering:

| Stored value | Why it is kept |
|---|---|
| Server, channel, parent-channel, and message IDs | Find the message, respect ignored thread parents, and show where it came from. |
| Author ID, display name, and bot flag | Show who wrote the original message. |
| Message content | Recover edits and deletions that Discord no longer has cached. |
| Attachment ID, filename, Discord URL, byte size, and content type | Include attachment links and metadata in deletion logs. Attachment bytes are not downloaded. |
| Creation and edit timestamps | Preserve event context. |
| Expiration timestamp | Enforce configured snapshot retention. |

After a deletion log is posted successfully, the corresponding saved message
is removed immediately. If posting fails, the saved message stays until its
normal expiration time; failed posts are not retried automatically. Kimi runs
a daily job that removes expired rows.

After an edit, the module asks Discord for the current message so its saved
attachment list stays accurate. If Discord cannot provide it, the module keeps
the new text but clears the old attachment list. It is better to omit an
attachment than to show one the author already removed.

When upgrading from an older version, migration
`002_add_parent_channel_to_message_snapshots` adds the thread's parent channel
to saved messages. It clears the existing temporary message cache once because
those older rows do not contain enough information to apply parent-channel
exclusions safely. New messages refill the cache normally.

Clearing a server's logging channel, adding an ignored channel, lowering the
retention period, or disabling the module also cleans up messages already in
the table. Removing `discord_logging` from `KIMI_MODULES` or uninstalling the
package is different: the module is no longer running, so it cannot clean up
its table. Existing rows remain until the module runs again or an operator
removes the table. Backups may keep copies according to your backup policy.

For a clean uninstall, first remove `logging_channel_id` from every server's
settings while the module is still running. Wait for Kimi to notice the change,
or restart it once, so the saved messages are removed. Then remove
`discord_logging` from `KIMI_MODULES` and uninstall the package. Waiting for
retention to expire after uninstalling will not help because the cleanup job is
no longer running.

### Data kept in memory or posted to Discord

- Invite codes, counters, limits, channel IDs, and inviter IDs are held in
  process memory for attribution. They are not written to the module table and
  disappear when the process exits.
- Audit embeds posted to Discord contain the selected event details and follow
  the Discord server's channel and retention policies; pruning the local table
  does not delete those posts.
- The module talks to Discord only through Kimi's public module API. Attachment
  and invite URLs may appear as links, but the module does not open or download
  them.
- Outside Discord itself, this package does not send module data to an LLM,
  analytics service, or any other third party.

Kimi modules run as Python inside the Kimi process; they are not sandboxed.
The public API makes their access easier to understand and review, but you
should still review a module's code before enabling it.

## Troubleshooting

| Symptom | Likely cause and check |
|---|---|
| Startup says the entry point is missing | Install this package into the interpreter used by the service, not a different shell environment. Check with `.venv/bin/python -c "import importlib.metadata as m; print([e.name for e in m.entry_points(group='kimi_agent.modules')])"` from `bot/`. |
| Module is listed as soft-disabled | Enable both intents in the dotenv and Discord Developer Portal, then restart. `/modules status` names the missing capability. |
| Module is healthy but nothing is posted | Confirm the server settings file has `logging_channel_id`, the channel is not ignored, the relevant `log_*` field is true, and the bot can view/send/embed there. |
| Delivery health is degraded or Discord returns `403 Missing Access` | Recheck the bot's channel overwrites and role permissions for the configured logging channel. Health failures are tracked separately for each server, so success elsewhere does not hide this fault. |
| Invite create/delete logs are absent | Grant **Manage Channels** where invites are created. Discord does not emit those events to the bot otherwise. |
| Join logs say the invite is unknown | Grant **Manage Server**, then restart so the module can record the starting invite counts. Vanity URLs and several changes at once can still produce an unknown result. |
| An edit/delete says its snapshot was missing | The message predates startup, was in an ignored channel, expired, was not visible to the bot, or arrived while Message Content Intent was unavailable. |
| `/logging setup` is absent | Confirm the module started and Discord command sync succeeded. The command is available only to staff in a server. |
| A later `uv sync` made the module disappear | Reinstall the editable checkout or wheel after syncing Kimi. |

## Developer guide

The module imports only Kimi's public module API. Its tests use the fakes in
`kimi_agent_module_api.testing`, so you can work on this repository without
loading the full bot. Read Kimi's
[`docs/modules.md`](https://github.com/webhead2oo9/kimi-agent/blob/main/docs/modules.md)
for the full module lifecycle and API contracts.

### Code map

| File | What it does |
|---|---|
| `pyproject.toml` | Package metadata, dependencies, and the `kimi_agent.modules` entry point. |
| `.github/workflows/ci.yml` | Runs formatting, lint, type checks, tests, a dependency audit, and a package build. |
| `MANIFEST.in` | Lists the extra files that belong in the source package, including the tests. |
| `src/kimi_agent_discord_logging/spec.py` | Describes the module to Kimi and lists the Discord events and actions it needs. |
| `src/kimi_agent_discord_logging/guild_settings.py` | Defines and validates the settings available to each server. |
| `src/kimi_agent_discord_logging/migrations.py` | Creates and upgrades the module's database table. |
| `src/kimi_agent_discord_logging/snapshots.py` | Reads and writes temporary message copies through Kimi's `ModuleStorage` API. |
| `src/kimi_agent_discord_logging/invites.py` | Tracks invite counters and avoids guessing when the result is unclear. |
| `src/kimi_agent_discord_logging/renderer.py` | Builds Discord embeds and keeps them within Discord's length limits. |
| `src/kimi_agent_discord_logging/module.py` | Starts and stops the module, handles events, sends logs, and reports health. |
| `tests/conftest.py` | Starts the module with the SDK's fake Discord and in-memory database. |
| `tests/test_spec.py` | Checks that Kimi can load the module and that its declarations are valid. |
| `tests/test_module.py` | Tests the main logging behavior through the public API. |
| `tests/test_invites.py` | Covers uncertain and out-of-order invite cases. |
| `tests/test_migrations.py` | Checks upgrades from an existing installation. |
| `tests/test_renderer.py` | Checks embed limits and user-provided names and filenames. |
| `tests/test_storage_lifecycle.py` | Checks retention, ignored channels, and cleanup when the module is disabled. |

When Kimi starts, it loads `SPEC`, checks the declarations, creates the module,
runs its migrations, and calls `start()`. During shutdown it calls `close()`,
which unregisters event handlers and commands. The daily cleanup uses Kimi's
scheduler, so the job survives a restart.

Each Discord server has its own message and invite locks. Events from the same
server stay in order, while a slow request in one server does not hold up the
others. Settings changes use those same locks.

The module asks Kimi only for the access it uses:

- capabilities `discord.members.v1` and `discord.message_content.v1`;
- Discord actions `send_message`, `fetch_message`, `fetch_channel`, and
  `fetch_invites`;
- seven normalized `discord.*` event topics;
- no direct bot or database access, outbound HTTP hosts, LLM tools, shared
  services, or dependencies on other modules.

### Local development

Install the locked development environment and run every check from this
repository's root:

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

The lock file installs `kimi-agent-module-api` from PyPI on purpose. This helps
catch accidental imports from a nearby Kimi checkout. If you need to test an
unreleased API change, use a temporary local source override and do not commit
it as the package dependency.

When changing the module:

1. Keep the version in `pyproject.toml` and `spec.VERSION` equal.
2. Never rename, reorder, or edit a released migration. Add a new, uniquely
   named migration for each database change; Kimi rejects changed migration
   history.
3. Add declarations before using a new event, Discord action, HTTP host, or
   service, and extend `test_declarations_pass_host_preflight`.
4. Update the settings reference and privacy disclosure whenever observed or
   stored data changes.
5. Run the checks, build the package, then install the wheel or editable
   checkout into a sandbox Kimi instance for a real startup and Discord test.

## License and name

MIT. See [LICENSE](LICENSE).

This is an independent community module for the Kimi Discord assistant. It is
not affiliated with, endorsed by, or sponsored by Moonshot AI or any of its
Kimi products. In this repository, “Kimi” means the Discord bot that loads the
module.
