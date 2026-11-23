# agent-use-adapters usage guide

Start with the [README](../README.md) for what problems it solves. This guide covers installation details, failure reasons and the trust model.

## What the installer does

The installer links `config/dispatcher.json` into `<config dir>/agent-use/dispatcher.json`. The config dir defaults to `$HOME/.config`; only a nonempty, absolute `XDG_CONFIG_HOME` replaces it. A relative value is ignored and `$HOME/.config` is used. Repeating installation is safe; an existing
different file or symlink is never overwritten. To use this registry temporarily,
set `AGENT_USE_CONFIG` to the full path of this repository's `config/dispatcher.json`,
or merge registrations yourself. Keep this clone in place while its link is installed.
The core resolves the link before expanding `{config_dir}`, so adapter paths work.

## Failure reasons

| Reason | Meaning |
| --- | --- |
| `credential_missing` | The CLI is missing or its official authentication is required. |
| `quota_exhausted` | The account or service usage allowance is exhausted. |
| `rate_limited` | Requests exceed the service's rate limit. |
| `network_failed` | A connection, timeout, DNS, or classified server error occurred. |
| `upstream_failed` | The CLI or service failed without a more specific classification. |
| `empty_response` | The CLI completed without usable text. |
| `timeout` | The adapter's execution budget expired. |
| `invalid_request` | The protocol, options, model, or another required request value is invalid. |
| `free_tier_rejected` | OpenCode's upstream rejected access to its free tier. |
| `payment_required` | OpenCode's upstream requires payment or additional funds. |
| `tool_use_rejected` | OpenCode attempted tools and produced no usable final text. |
| `interrupted` | Execution was cancelled or received a termination signal; the core stops the entire fallback chain. |

All adapters return `interrupted`. Only `free_tier_rejected`, `payment_required`,
and `tool_use_rejected` are OpenCode-specific. A fatal failure stops that provider's
attempt; it does not approve a draft or authorize spending.

## Trust model

These adapters do not read or save account credentials; authentication is entirely
handled by each official CLI. Tasks are sent to those CLIs and their upstream
services, and task/response artifacts are local plaintext. Review adapters and
registries before running them; CLI safety settings do not remove their process's
normal user privileges. OpenCode session deletion is best-effort.

Child processes receive `AIW_NESTED=1` to identify them as delegated executors.
This is recognized when used with [aiw](https://github.com/Jerryx333/aiw)
and has no effect otherwise. For aiw workflows, install these adapters and
point its dispatch setup at the agent-use core.

## ACP engines (opt-in)

Select `opencode-acp` or `qwen-acp` explicitly; neither is in the default profile.
`adapters/acp_adapter.py` uses Python 3.11's standard library and ACP v1 over
newline-delimited JSON-RPC. `options.agent` is required: `opencode`, `qwen`, `agy`,
`gemini`, `cursor`, `grok`, or `kiro`. Override just the executable path with
`ACP_<AGENT>_BIN` (for example `ACP_QWEN_BIN`); it is never evaluated as a shell
command. Other engines' installed versions and restrictions are unverified.

Requests use the [agent-use adapter envelope](https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md#adapter-protocol).
For `access=none` (the default), omit `workdir`: the engine runs in an empty
temporary directory and all permission requests are denied. For `ro`/`rw`, supply
an absolute existing `workdir`. `ro` approves only `read`/`search` tool kinds;
`rw` approves all kinds. Decisions prefer once-only options; missing kinds fail
closed for `ro`, and missing usable options return `cancelled`. ACP permissions
depend on an honest engine reporting its tool kinds and requesting permission;
this is not an OS sandbox. Unknown client methods, including `fs/*` and
`terminal/*`, return JSON-RPC `-32601`.

The client always sends `mcpServers: []`, never implements MCP, and never resumes
sessions. OpenCode runs with an isolated temporary home/config/data/cache/state,
including `OPENCODE_TEST_HOME`, `OPENCODE_DISABLE_EXTERNAL_SKILLS`,
`OPENCODE_DISABLE_CLAUDE_CODE`, `OPENCODE_DISABLE_PROJECT_CONFIG`, and `OPENCODE_PURE`.
External skills, user/project instructions and external plugins are disabled.
Configuration sets sharing disabled, autoupdate false, empty MCP and `permission: {"*": "ask"}`.
The ACP client enforces `none`/`ro`/`rw` on these requests. Do not replace `ask`
with engine-side `deny`: installed OpenCode 1.19.6 with `opencode/mimo-v2.6-flash-free`
stalled in that configuration; changing only to `ask` returned PONG with the same reader.
Local account/model settings
are not imported (environment credentials, if supplied, remain available). Qwen
uses empty setting sources, strict empty MCP configuration, an empty built-in tool
list for `none`, `Read,Grep,Glob` for `ro`, and explicit permission settings.
It also sets `--disable-builtin-skills`, `skills.enabled=false`, empty
`context.fileName`, and disables auto memory, hooks and agents. These options were
checked against the installed Qwen 1.0.0-preview.1 implementation; online docs may
describe a different version. Qwen retains the user home for existing login and
may write `~/.qwen/projects`; global/managed instructions and plugin isolation
remain **unverified**. OpenCode system-managed configuration isolation is also
unverified. `status.json` records these limits under `isolation`, and records
observed `available_commands_update` names under `available_commands` for auditing.
Missing notifications are not treated as an empty list.
Other engines may load their own configuration: their MCP/tool isolation
is **unverified** and they require a suitably restricted launcher before trusted
workspace use. An empty ACP MCP list alone does not suppress engine-local MCP.

An explicit `model` must be an exact advertised value of a `category=model`,
`type=select` [config option](https://agentclientprotocol.com/protocol/v1/session-config-options),
including grouped values. The adapter calls `session/set_config_option` and checks
the returned value. Unsupported selection fails with `invalid_request`; there is
no fallback to `session/set_model` or another model. Without `model`, the engine
keeps its default. `opencode-acp` defaults to an explicitly named free model;
unlike the original `opencode` adapter, ACP itself has no free-model allowlist.
The Qwen registration names `qmodel_latest` (advertised by the installed CLI's
`--list-models`); its ACP selection still needs live verification.

Authentication errors return `credential_missing`; advertised login methods alone
do not mean login is needed. The adapter never logs in.
Qwen's complimentary-credit exhaustion/paid-credit continuation errors return
`payment_required`, without continuing payment. Error classification reads message
and nested diagnostic strings, not numeric RPC codes; successful answer text is
never classified as an error. Version mismatches or
malformed RPC return `invalid_protocol`; crashes return `upstream_failed` (or a
classified CLI error). A timeout sends `session/cancel`, allows a short grace
period, then terminates and reaps the process group. `response.txt` and
`status.json` are written on completion; failures never publish partial text as
success. Tool events and approval decisions are recorded in bounded
`trajectory.jsonl`. Engines may keep their own session history; no session deletion
or resumption is implemented. Offline tests use `tests/fixtures/fake_acp_agent.py`.

Run offline checks with `bash scripts/test.sh`. Opt-in live checks are
`python3 -B scripts/test_acp_live.py --output /tmp/acp-smoke` (up to three
180-second calls, sequentially). They retain requests and results and require an
observed write rejection for the read-only case; a claimed refusal alone does not
pass. Neither command installs engines or authenticates accounts.
If Qwen credits are exhausted, add `--expect-qwen-payment-required` to require
that exact failure reason for both Qwen cases; this does not validate read-only
functionality with an available balance.
