# agent-use-adapters

[中文](README.md) | [English](README.en.md)

Connect the Codex and Claude Code CLIs you are already logged in to, plus OpenCode's free models (no login needed), to [agent-use](https://github.com/Jerryx333/agent-use). Install once, with no handwritten configuration, then hand tasks to them with one command; if one is unavailable, the next one takes over.

## What problems does it solve?

- **The agent-use core ships only demo adapters.** To use real models you would write your own registry and learn each CLI's arguments and failure modes.
- **Calling these CLIs directly from scripts is risky.** It is easy to leave tool access on, write files into your working directory, or pile up session history.

## What changes after installation?

- **One install command, three CLIs ready.** Logged-in Codex and Claude Code work right away; OpenCode's free models need no login.
- **If one is unavailable, the next takes over.** The default order is Codex → Claude Code → OpenCode, or you can use just one. If you interrupt a run, the whole chain stops instead of trying the next one.
- **Conservative defaults.** Every task runs in an empty temporary directory: Codex uses a read-only sandbox, Claude Code has all tools disabled, OpenCode allows only free models and denies tool calls; sessions are not kept (best-effort for OpenCode).
- **Consistent failure reasons.** Missing login, exhausted quota, rate limits, timeouts and more each have a fixed reason code, so callers can decide what to do next.

## Questions you may have

**Does it cost money?** Calls use the selected CLI's own account and quota. OpenCode allows only `-free` models; if the upstream requires payment, the call fails instead of switching to a paid model.

**Does it touch my credentials?** No. It never reads or stores them; each CLI handles its own authentication.

**Where do tasks go?** To the selected CLI and its upstream service. Tasks and answers are saved as plain text on your machine.

**Will it overwrite my existing configuration?** No. Repeating installation is safe, and an existing different file or symlink is never overwritten.

## 30-second setup

Requires Python 3.11+, Bash, and POSIX (macOS/Linux). Install the official CLIs first. Existing official CLI logins work directly; no additional Python packages are needed.

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

Omit `providers` to try the default order: Codex → Claude → OpenCode, falling back
when a route fails (except after an interruption). Named profiles are `default`, `codex-only`, `claude-only`, and
`opencode-only`; use `"profile":"opencode-only"` instead of `providers` to select one.

Where the installer puts the registry and how to use your own: see the [guide](docs/guide.md#what-the-installer-does).

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

## More documentation

- [Usage guide](docs/guide.md): installation details, failure reasons, trust model.
- The agent-use core's [dispatcher protocol](https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md).

## Contributing

Run offline checks with `bash scripts/test.sh`. Submit changes through PRs; see [CONTRIBUTING.md](CONTRIBUTING.md) for development. License: [Apache-2.0](LICENSE).
