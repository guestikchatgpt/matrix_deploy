#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run upgrade.sh as root' >&2; exit 1; }

printf 'A backup of the current Matrix state will be created, then preflight will re-resolve the latest stable upstream versions and apply exactly those.\n'
printf 'PostgreSQL stays on its currently allowed major version; only patch/minor updates within that major are applied automatically.\n'
read -r -p 'Proceed with the upgrade? [y/N]: ' answer
[[ "$answer" =~ ^[Yy]$ ]] || { echo 'Upgrade cancelled.'; exit 0; }

"${REPO_ROOT}/backup.sh"
exec "${REPO_ROOT}/converge.sh" --refresh-versions
