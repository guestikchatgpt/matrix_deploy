#!/usr/bin/env bash
set -Eeuo pipefail

readonly INSTALL_ROOT="${MATRIX_DEPLOY_INSTALL_ROOT:-/opt/matrix-deploy}"
readonly SOURCE_DIR="${INSTALL_ROOT}/source"
readonly ETC_DIR="${MATRIX_DEPLOY_ETC_DIR:-/etc/matrix-deploy}"
readonly BACKUP_DIR="${MATRIX_DEPLOY_BACKUP_DIR:-/var/backups/matrix-deploy}"
readonly ANSIBLE_DIR="${SOURCE_DIR}/ansible"
readonly CLI_PATH="${MATRIX_DEPLOY_CLI_PATH:-/usr/local/sbin/matrix-deploy}"

log() { printf '[matrix-deploy] %s\n' "$*"; }
fatal() { printf '[matrix-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() { [[ ${EUID} -eq 0 ]] || fatal "This command must run as root."; }
require_ubuntu() {
  [[ -r /etc/os-release ]] || fatal "/etc/os-release is missing."
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || fatal "Only Ubuntu is supported."
  [[ "${VERSION_ID:-}" == "24.04" ]] || fatal "Ubuntu 24.04 LTS is required; found ${PRETTY_NAME:-unknown}."
}
require_resources() {
  local mem_kb cpus disk_kb
  mem_kb="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
  cpus="$(getconf _NPROCESSORS_ONLN)"
  disk_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
  (( mem_kb >= 3900000 )) || fatal "At least 4 GiB RAM is required."
  (( cpus >= 2 )) || fatal "At least 2 vCPU are required."
  (( disk_kb >= 20971520 )) || fatal "At least 20 GiB free disk space is required on /."
}
ensure_packages() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ansible-core ca-certificates curl python3 python3-pip python3-venv
}
install_collections() {
  cd "$ANSIBLE_DIR"
  ansible-galaxy collection install -r requirements.yml
}
install_source() {
  local self_root
  self_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  mkdir -p "$INSTALL_ROOT" "$ETC_DIR" "$BACKUP_DIR"
  if [[ "$self_root" != "$SOURCE_DIR" ]]; then
    rm -rf "${SOURCE_DIR}.new"
    mkdir -p "${SOURCE_DIR}.new"
    cp -a "$self_root"/. "${SOURCE_DIR}.new"/
    if [[ -d "$SOURCE_DIR" ]]; then
      rm -rf "${SOURCE_DIR}.previous"
      mv "$SOURCE_DIR" "${SOURCE_DIR}.previous"
    fi
    mv "${SOURCE_DIR}.new" "$SOURCE_DIR"
  fi
  printf '%s\n' "${MATRIX_DEPLOY_RELEASE_TAG:-unknown}" >"${ETC_DIR}/installed-release"
}
install_cli() {
  install -m 0755 "${SOURCE_DIR}/scripts/matrix-deploy" "$CLI_PATH"
}
run_playbook() {
  cd "$ANSIBLE_DIR"
  ansible-playbook playbooks/site.yml
}
run_verifier() {
  if command -v matrix-stack-verify >/dev/null 2>&1; then
    matrix-stack-verify
  elif [[ -x /usr/local/sbin/matrix-stack-verify ]]; then
    /usr/local/sbin/matrix-stack-verify
  else
    log "Verifier command is not installed yet; relying on the Ansible verification role."
  fi
}

main() {
  require_root
  require_ubuntu
  require_resources
  install_source
  install_cli
  ensure_packages
  install_collections
  run_playbook
  run_verifier
  log "Installed matrix-deploy CLI: ${CLI_PATH}"
}

main "$@"
