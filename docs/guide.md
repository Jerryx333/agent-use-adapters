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
