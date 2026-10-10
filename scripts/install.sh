#!/usr/bin/env bash
# Register these adapters without replacing an existing registry.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
registry="$repo_root/config/dispatcher.json"
config_base="$HOME/.config"
if [[ -n "${XDG_CONFIG_HOME:-}" && "$XDG_CONFIG_HOME" == /* ]]; then
    config_base="$XDG_CONFIG_HOME"
fi
target="$config_base/agent-use/dispatcher.json"
mkdir -p "$(dirname "$target")"
python3 -B - "$target" "$registry" <<'PY'
import os
from pathlib import Path
import sys
target, registry = sys.argv[1:]
try:
    os.symlink(registry, target)
except FileExistsError:
    try:
        matches = Path(target).is_symlink() and Path(target).resolve(strict=True) == Path(registry).resolve(strict=True)
    except (OSError, RuntimeError):
        matches = False
    if not matches:
        print(f"Refusing to replace the existing registry: {target}", file=sys.stderr)
        print(f'Use AGENT_USE_CONFIG="{registry}" temporarily, or merge the registries yourself.', file=sys.stderr)
        sys.exit(1)
    print(f"Already installed: {target}")
else:
    print(f"Installed: {target}")
PY
cat <<'NEXT'
Next, from the agent-use core directory:
  python3 -B scripts/agent_use.py list
Authenticate through the official CLIs:
  Codex: codex login
  Claude Code: start claude, then use /login
  OpenCode: free Zen models need no login
NEXT
