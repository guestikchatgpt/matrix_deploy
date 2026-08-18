#!/usr/bin/env bash
set -euo pipefail

readonly ANSIBLE_CORE_VERSION="2.21.2"
readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly VENV_DIR="${REPO_ROOT}/.venv"

log() {
  printf '[bootstrap] %s\n' "$*"
}

fatal() {
  printf '[bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ ${EUID} -ne 0 ]]; then
  fatal "запустите bootstrap.sh от root (или через sudo)"
fi

if [[ ! -r /etc/os-release ]]; then
  fatal "/etc/os-release отсутствует"
fi

# shellcheck disable=SC1091
source /etc/os-release

if [[ ${ID:-} != "ubuntu" || ${VERSION_CODENAME:-} != "noble" ]]; then
  fatal "поддерживается Ubuntu 24.04 LTS (noble); обнаружено ${PRETTY_NAME:-unknown}"
fi

export DEBIAN_FRONTEND=noninteractive

log "устанавливаю bootstrap-зависимости"
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  dnsutils \
  git \
  iproute2 \
  jq \
  openssl \
  python3 \
  python3-pip \
  python3-venv

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  log "создаю Python venv"
  python3 -m venv "${VENV_DIR}"
fi

log "устанавливаю ansible-core ${ANSIBLE_CORE_VERSION}"
"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check --upgrade pip wheel
"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check "ansible-core==${ANSIBLE_CORE_VERSION}"

log "устанавливаю pinned Ansible collections"
"${VENV_DIR}/bin/ansible-galaxy" collection install \
  -r "${REPO_ROOT}/ansible/requirements.yml" \
  --force

log "проверяю Ansible"
"${VENV_DIR}/bin/ansible-playbook" --version | sed -n '1,3p'

exec "${REPO_ROOT}/deploy.sh" "$@"
