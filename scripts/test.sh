#!/usr/bin/env bash
# Run all offline tests in an isolated home directory.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$repo_root"
temporary_home="$(mktemp -d "${TMPDIR:-/tmp}/agent-use-adapters-test.XXXXXX")"
trap 'rm -rf "$temporary_home"' EXIT
export HOME="$temporary_home"
unset AGENT_USE_CONFIG XDG_CONFIG_HOME
python3 -B -m unittest discover -s tests
while IFS= read -r -d '' script; do
    bash -n "$script"
done < <(find scripts tests -type f -name '*.sh' -print0)
bash tests/test_install.sh
