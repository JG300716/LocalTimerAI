# LocalFlow integrations

[Polski](INTEGRATIONS.md) | [English](INTEGRATIONS.en.md)

## IDE tracking through existing WakaTime plugins

LocalFlow accepts heartbeats from Rider/JetBrains, VS Code, and other editor
plugins that use the WakaTime CLI. The plugin still identifies the project,
language, branch, and editing events. The Windows process tracker supplies the
application name and activity time; it cannot replace that information.

Base URL for configuration: `http://127.0.0.1:48763/api/v1` (adjust the port to
match the running application). Do not append `heartbeats.bulk`.

**mirror** mode preserves the existing WakaTime settings and adds the following
to `%USERPROFILE%\.wakatime.cfg`:

```ini
[api_urls]
.* = http://127.0.0.1:48763/api/v1|YOUR_LOCALFLOW_KEY
```

The current CLI supports forwarding heartbeats to multiple servers. If you use
an older CLI, update the plugin/CLI or choose **local** mode, which sets:

```ini
[settings]
api_url = http://127.0.0.1:48763/api/v1
api_key = YOUR_LOCALFLOW_KEY
```

In the dashboard, select **Główny zapis lokalnie** ("Use local as the primary
destination"). This option sends the API mode `local-only`, which maps to the
`mode="local"` parameter of `configure_wakatime(...)`.
The function changes the configuration only when explicitly invoked.
Before saving, it creates a `.localflow-backup-DATE` copy next to the
configuration file. It is not called when the tracker starts. Restart your IDE
after making the change. API settings overridden in the editor itself or through
CLI arguments may take precedence over the configuration file.

The supported submission protocol is POST to
`/api/v1/users/current/heartbeats` and
`/api/v1/users/current/heartbeats.bulk` (up to 25 entries per batch).
Editor information comes from the `User-Agent` header.

Sources: [WakaTime CLI: configuration and multiple API URLs](https://github.com/wakatime/wakatime-cli/blob/develop/USAGE.md),
[heartbeat protocol](https://wakatime.com/developers#heartbeats).

## AI models: local Codex and Claude Code metadata

The collector reads `.jsonl` files from the directories specified in `config.json`:

```json
{
  "ai": {
    "enabled": true,
    "poll_seconds": 15,
    "codex_paths": ["~/.codex/sessions"],
    "claude_paths": ["~/.claude/projects"]
  }
}
```

The first run imports available historical files. Later scans read appended
bytes; offsets and minimal context are stored in the LocalFlow database.
Persistent event identifiers prevent duplicate imports. You can also point to
a specific JSONL file, for example to import an archived copy.

Only timestamps, durations, the project name derived from its directory, the
tool name, model, token counts, and the type of time measurement are stored in
the database. Prompt text, responses, code, tool arguments, and tool results
are not copied into the database. The collector must parse transcript files,
but it does not transmit them over the network.

Codex: `token_usage_record` entries with `response_id` and `usage` are preferred.
Older logs with `event_msg.token_count` use increments of the
`total_token_usage` counter; mirrored events are not counted again.
Time comes from matching `task_started` with `task_complete` or an interruption.

Claude Code: token counts come from `assistant.message.usage`. Subsequent
fragments of the same response contribute only counter increments. Time is
available when the log contains `system.turn_duration`. Without that
measurement, time is unavailable; it does not mean zero seconds of inference.

**AI time is the observed elapsed time of an agent turn**, including tools and
any waiting within that turn. It is not the exact time spent computing by the
model. It does not measure the entire conversation from beginning to end and
should not be added to human activity time as an extra hour of work.
An unfinished turn receives a duration once its completion is recorded in the
log.

`tokens_input` includes all input tokens; `tokens_cached` is a subset of those
tokens. Cached tokens are not added again to the input + output total. For
Claude, normalization includes cache reads and cache creation, which are
separate fields in its raw metadata. Cost remains unknown (`null`): token usage
under a subscription is not an API bill.

Local file schemas may change between tool versions. The parser recognizes the
variants described above, ignores other events, and reports read errors without
exposing transcript content. It does not cover sessions that run exclusively
in the cloud, other AI clients without local metadata, or full GitHub Copilot
telemetry. Those sources require their own adapters.

Sources: [official Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Claude Code: usage tracking and cost limitations](https://code.claude.com/docs/en/costs).
The exact Codex parser mapping was also checked against the structure of local
metadata without copying conversation content into the project materials.
