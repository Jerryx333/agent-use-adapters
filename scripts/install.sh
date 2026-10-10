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
if [[ -L "$target" ]] && python3 -B - "$target" "$registry" <<'PY'
from pathlib import Path
import sys
try:
    matches = Path(sys.argv[1]).resolve(strict=True) == Path(sys.argv[2]).resolve(strict=True)
except (OSError, RuntimeError):
    matches = False
sys.exit(0 if matches else 1)
PY
then
    echo "Already installed: $target"
elif [[ -e "$target" || -L "$target" ]]; then
    echo "Refusing to replace the existing registry: $target" >&2
    echo "Use AGENT_USE_CONFIG=\"$registry\" temporarily, or merge the registries yourself." >&2
    exit 1
else
    ln -s "$registry" "$target"
    echo "Installed: $target"
fi
cat <<'NEXT'
Next, from the agent-use core directory:
  python3 -B scripts/agent_use.py list
Authenticate through the official CLIs:
  Codex: codex login
  Claude Code: start claude, then use /login
  OpenCode: free Zen models need no login
NEXT
