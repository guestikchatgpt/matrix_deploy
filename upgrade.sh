#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run upgrade.sh as root' >&2; exit 1; }

printf 'This will back up the current Matrix state and then apply the versions/configuration pinned in this checkout.\n'
read -r -p 'Continue with upgrade? [y/N]: ' answer
[[ "$answer" =~ ^[Yy]$ ]] || { echo 'Upgrade cancelled.'; exit 0; }

"${REPO_ROOT}/backup.sh"
exec "${REPO_ROOT}/converge.sh"
