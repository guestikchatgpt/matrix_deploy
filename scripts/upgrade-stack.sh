#!/usr/bin/env bash
set -Eeuo pipefail
readonly SOURCE_DIR="${MATRIX_DEPLOY_SOURCE_DIR:-/opt/matrix-deploy/source}"
readonly BACKUP_DIR="${MATRIX_DEPLOY_BACKUP_DIR:-/var/backups/matrix-deploy}"
fatal(){ printf '[matrix-deploy] ERROR: %s\n' "$*" >&2; exit 1; }
log(){ printf '[matrix-deploy] %s\n' "$*"; }
[[ ${EUID} -eq 0 ]] || fatal "matrix-deploy upgrade must run as root."
stamp="$(date -u +%Y%m%dT%H%M%SZ)"; target="${BACKUP_DIR}/${stamp}"; mkdir -p "$target"
[[ -d /opt/matrix ]] && tar -C /opt -czf "${target}/matrix-files.tar.gz" matrix
[[ -d /etc/matrix-deploy ]] && tar -C /etc -czf "${target}/matrix-deploy-etc.tar.gz" matrix-deploy
printf '%s\n' "$(cat /etc/matrix-deploy/installed-release 2>/dev/null || printf unknown)" >"${target}/installer-release"
log "Pre-upgrade backup created: ${target}"
[[ -x "${SOURCE_DIR}/scripts/resolve-versions.sh" ]] || fatal "Dynamic version resolver is not implemented yet. No Matrix upgrade was attempted."
"${SOURCE_DIR}/scripts/resolve-versions.sh" --upgrade
cd "${SOURCE_DIR}/ansible"
ansible-playbook playbooks/site.yml
/usr/local/sbin/matrix-stack-verify
