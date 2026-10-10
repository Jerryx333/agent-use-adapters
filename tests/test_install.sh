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
expect_refusal() {
    local status=0
    bash "$repo_root/scripts/install.sh" || status=$?
    if [[ "$status" -ne 1 ]]; then
        echo "Expected refusal with exit code 1, got $status" >&2
        exit 1
    fi
}
bash "$repo_root/scripts/install.sh"
[[ -L "$target" && "$(readlink "$target")" == "$registry" ]]
bash "$repo_root/scripts/install.sh"
[[ "$(readlink "$target")" == "$registry" ]]
rm "$target"
printf '%s\n' 'keep this registry' > "$target"
expect_refusal
[[ ! -L "$target" && "$(cat "$target")" == 'keep this registry' ]]
# A directory must not receive a nested symlink.
rm "$target"
mkdir "$target"
expect_refusal
[[ -d "$target" && ! -L "$target" ]]
[[ -z "$(ls -A "$target")" ]]
rmdir "$target"
# A different or dangling symlink must also survive unchanged.
ln -s "$temporary_home/other.json" "$target"
expect_refusal
[[ -L "$target" && ! -e "$target" && "$(readlink "$target")" == "$temporary_home/other.json" ]]
printf '%s\n' 'keep other registry' > "$temporary_home/other.json"
expect_refusal
[[ "$(readlink "$target")" == "$temporary_home/other.json" ]]
[[ "$(cat "$temporary_home/other.json")" == 'keep other registry' ]]
# Relative and empty XDG values fall back to HOME; absolute values are honored.
rm "$target"
XDG_CONFIG_HOME=relative bash "$repo_root/scripts/install.sh"
[[ "$(readlink "$target")" == "$registry" ]]
XDG_CONFIG_HOME= bash "$repo_root/scripts/install.sh"
XDG_CONFIG_HOME="$temporary_home/xdg" bash "$repo_root/scripts/install.sh"
[[ "$(readlink "$temporary_home/xdg/agent-use/dispatcher.json")" == "$registry" ]]
# Both repository and XDG paths may contain spaces.
space_repo="$temporary_home/repository with spaces"
space_xdg="$temporary_home/xdg with spaces"
mkdir -p "$space_repo/scripts" "$space_repo/config"
cp "$repo_root/scripts/install.sh" "$space_repo/scripts/install.sh"
cp "$registry" "$space_repo/config/dispatcher.json"
XDG_CONFIG_HOME="$space_xdg" bash "$space_repo/scripts/install.sh"
space_target="$space_xdg/agent-use/dispatcher.json"
[[ -L "$space_target" && "$(readlink "$space_target")" == "$space_repo/config/dispatcher.json" ]]
XDG_CONFIG_HOME="$space_xdg" bash "$space_repo/scripts/install.sh"
[[ "$(readlink "$space_target")" == "$space_repo/config/dispatcher.json" ]]
echo 'Installation tests passed.'
