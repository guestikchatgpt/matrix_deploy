#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run upgrade.sh as root' >&2; exit 1; }

printf 'Будет создан backup текущего Matrix-состояния, затем preflight заново определит актуальные stable-версии upstream и применит именно их.\n'
printf 'PostgreSQL останется в текущем разрешённом major; автоматически обновляется только patch/minor внутри этого major.\n'
read -r -p 'Продолжить обновление? [y/N]: ' answer
[[ "$answer" =~ ^[Yy]$ ]] || { echo 'Обновление отменено.'; exit 0; }

"${REPO_ROOT}/backup.sh"
exec "${REPO_ROOT}/converge.sh" --refresh-versions
