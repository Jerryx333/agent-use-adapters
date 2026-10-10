#!/usr/bin/env bash
# Exercise installation without touching the user's registry.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
temporary_home="$(mktemp -d "${TMPDIR:-/tmp}/adapters-install-test.XXXXXX")"
trap 'rm -rf "$temporary_home"' EXIT
export HOME="$temporary_home"
unset AGENT_USE_CONFIG XDG_CONFIG_HOME
registry="$repo_root/config/dispatcher.json"
target="$HOME/.config/agent-use/dispatcher.json"
bash "$repo_root/scripts/install.sh"
[[ -L "$target" && "$(readlink "$target")" == "$registry" ]]
bash "$repo_root/scripts/install.sh"
[[ "$(readlink "$target")" == "$registry" ]]
rm "$target"
printf '%s\n' 'keep this registry' > "$target"
if bash "$repo_root/scripts/install.sh"; then
    echo 'Expected refusal for an existing file' >&2
    exit 1
fi
[[ ! -L "$target" && "$(cat "$target")" == 'keep this registry' ]]
# A different or dangling symlink must also survive unchanged.
rm "$target"
ln -s "$temporary_home/other.json" "$target"
if bash "$repo_root/scripts/install.sh"; then
    echo 'Expected refusal for another symlink' >&2
    exit 1
fi
[[ "$(readlink "$target")" == "$temporary_home/other.json" ]]
# Relative and empty XDG values fall back to HOME; absolute values are honored.
rm "$target"
XDG_CONFIG_HOME=relative bash "$repo_root/scripts/install.sh"
[[ "$(readlink "$target")" == "$registry" ]]
XDG_CONFIG_HOME= bash "$repo_root/scripts/install.sh"
XDG_CONFIG_HOME="$temporary_home/xdg" bash "$repo_root/scripts/install.sh"
[[ "$(readlink "$temporary_home/xdg/agent-use/dispatcher.json")" == "$registry" ]]
echo 'Installation tests passed.'
