# agent-use-adapters

[简体中文](README.zh-CN.md)

Ready-to-use CLI adapters for [agent-use](https://github.com/Jerryx333/agent-use).
The core stays vendor-neutral; this repository supplies Codex, Claude Code, and
OpenCode integrations and a registry you can install with one command.

Requires Python 3.11+, Bash, and POSIX (macOS/Linux). Install the official CLIs
first. Existing official CLI logins work directly; no handwritten configuration
or additional Python packages are needed.

## 30-second setup

Clone both repositories next to each other:

```sh
git clone https://github.com/Jerryx333/agent-use.git
git clone https://github.com/Jerryx333/agent-use-adapters.git
cd agent-use-adapters
bash scripts/install.sh
cd ../agent-use
python3 -B scripts/agent_use.py list
printf '%s' '{"version":1,"task":"Explain what a CLI is in one sentence.","providers":["codex"],"output":"runs/first"}' | python3 -B scripts/agent_use.py run
```

Authenticate first if needed: `codex login`, or start `claude` and use `/login`.
OpenCode free Zen models need no login. A model run uses the selected CLI's quota.
Successful output is a draft: `state: staged_for_review`, `accepted: false`.
Inspect `runs/first/response.txt` and `runs/first/status.json`; use a fresh output
directory for a new task or retry.

The installer links `config/dispatcher.json` into
`${XDG_CONFIG_HOME:-$HOME/.config}/agent-use/dispatcher.json`. Only a nonempty,
absolute `XDG_CONFIG_HOME` is honored. Repeating installation is safe; an existing
different file or symlink is never overwritten. To use this registry temporarily,
set `AGENT_USE_CONFIG` to the full path of this repository's `config/dispatcher.json`,
or merge registrations yourself. Keep this clone in place while its link is installed.
The core resolves the link before expanding `{config_dir}`, so adapter paths work.

Omit `providers` to try the default order: Codex → Claude → OpenCode, falling back
when a route fails. Named profiles are `default`, `codex-only`, `claude-only`, and
`opencode-only`; use `"profile":"opencode-only"` instead of `providers` to select one.

## Adapters

| Adapter | Requirements / official login | Safety measures | Supported options |
| --- | --- | --- | --- |
| `codex` | Codex CLI; `codex login` | Empty temporary directory, read-only sandbox, ephemeral session | `effort`: `minimal`, `low` (default), `medium`, `high`, `xhigh` |
| `claude` | Claude Code; `/login` inside `claude` | Empty temporary directory, all tools disabled, one turn, no session persistence | `effort`: `low` (default), `medium`, `high`, `max` |
| `opencode` | OpenCode CLI with `run --pure`; no login for free Zen models | Only `-free` models; tool calls require authorization and are denied; deletes observed sessions after execution; tries `fallback_models` in order within a shared time budget | `fallback_models`: comma-separated `-free` model names |

Defaults: `gpt-6.1-sol`, `claude-opus-5-5`, and
`muse-spark-1.3-contributor-free`. OpenCode's default backups are
`mimo-v2.6-flash-free,longcat-2.5-preview-free`. Model availability depends on the CLI
and upstream service. Override executable locations with `CODEX_BIN`, `CLAUDE_BIN`,
or `OPENCODE_BIN` if needed.

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

All three adapters return `interrupted`. Only `free_tier_rejected`, `payment_required`,
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

See the core's [dispatcher protocol](https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md)
and [CONTRIBUTING.md](CONTRIBUTING.md) for development. Run offline checks with
`bash scripts/test.sh`.

Licensed under [Apache-2.0](LICENSE).
