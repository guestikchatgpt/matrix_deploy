#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./bootstrap.sh                 prepare Ansible environment and run interactive deploy.sh
#   ./bootstrap.sh --prepare-only  prepare Ansible environment only
#   ./bootstrap.sh --install       (used by install.sh) copy this source tree to
#                                  ${MATRIX_DEPLOY_INSTALL_ROOT}/source, install the
#                                  matrix-deploy CLI and continue from the installed copy

readonly ANSIBLE_CORE_VERSION="2.21.4"
readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly VENV_DIR="${REPO_ROOT}/.venv"
readonly INSTALL_ROOT="${MATRIX_DEPLOY_INSTALL_ROOT:-/opt/matrix-deploy}"
readonly INSTALLED_SOURCE="${INSTALL_ROOT}/source"
readonly RELEASE_MARKER="${INSTALL_ROOT}/installed-release"
readonly CLI_PATH="${MATRIX_DEPLOY_CLI_PATH:-/usr/local/sbin/matrix-deploy}"

log() {
  printf '[bootstrap] %s\n' "$*"
}

fatal() {
  printf '[bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

MODE=deploy
case "${1:-}" in
  '') ;;
  --prepare-only) MODE=prepare ;;
  --install) MODE=install ;;
  -h|--help)
    sed -n '4,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *) fatal "unknown argument: $1 (expected --prepare-only or --install)" ;;
esac

if [[ ${EUID} -ne 0 ]]; then
  fatal "run bootstrap.sh as root (or via sudo)"
fi

if [[ ! -r /etc/os-release ]]; then
  fatal "/etc/os-release is missing"
fi

# shellcheck disable=SC1091
source /etc/os-release

if [[ ${ID:-} != "ubuntu" || ${VERSION_CODENAME:-} != "noble" ]]; then
  fatal "only Ubuntu 24.04 LTS (noble) is supported; detected ${PRETTY_NAME:-unknown}"
fi

if [[ "$MODE" == install ]]; then
  # Installer mode: place the verified release tree at a stable location and
  # continue from there, so later `matrix-deploy` commands and converge/upgrade
  # always operate on the same source tree and .venv.
  if [[ "$REPO_ROOT" != "$INSTALLED_SOURCE" ]]; then
    log "installing sources into ${INSTALLED_SOURCE}"
    install -d -m 0755 "$INSTALL_ROOT"
    rm -rf "${INSTALLED_SOURCE}.new"
    mkdir -p "${INSTALLED_SOURCE}.new"
    cp -a "${REPO_ROOT}/." "${INSTALLED_SOURCE}.new/"
    rm -rf "${INSTALLED_SOURCE}.new/.venv"
    if [[ -d "$INSTALLED_SOURCE" ]]; then
      rm -rf "${INSTALLED_SOURCE}.previous"
      mv "$INSTALLED_SOURCE" "${INSTALLED_SOURCE}.previous"
    fi
    mv "${INSTALLED_SOURCE}.new" "$INSTALLED_SOURCE"
  fi
  release="${MATRIX_DEPLOY_RELEASE_TAG:-}"
  if [[ -z "$release" ]] && command -v git >/dev/null 2>&1 && \
     git -C "$INSTALLED_SOURCE" rev-parse --short HEAD >/dev/null 2>&1; then
    # Installed from a git clone rather than a release bundle.
    release="git-$(git -C "$INSTALLED_SOURCE" rev-parse --short HEAD)"
  fi
  printf '%s\n' "${release:-unknown}" > "$RELEASE_MARKER"
  install -m 0755 "${INSTALLED_SOURCE}/scripts/matrix-deploy" "$CLI_PATH"
  log "CLI installed: ${CLI_PATH}"
  if [[ "${MATRIX_DEPLOY_NONINTERACTIVE:-0}" == "1" ]]; then
    # Unattended install: prepare the controller only. The interactive
    # deploy (topology prompts, admin password) is started later with
    # ${INSTALLED_SOURCE}/bootstrap.sh.
    exec "${INSTALLED_SOURCE}/bootstrap.sh" --prepare-only
  fi
  exec "${INSTALLED_SOURCE}/bootstrap.sh"
fi

export DEBIAN_FRONTEND=noninteractive

log "installing bootstrap dependencies"
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
  python3-venv \
  skopeo

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  log "creating Python venv"
  # Use the noble distro interpreter explicitly: the pinned ansible-core needs
  # Python >= 3.12, and python3 on PATH may be an older local build.
  /usr/bin/python3.12 -m venv "${VENV_DIR}"
fi

log "installing ansible-core ${ANSIBLE_CORE_VERSION}"
"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check --upgrade pip wheel
"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check "ansible-core==${ANSIBLE_CORE_VERSION}"

log "installing pinned Ansible collections"
"${VENV_DIR}/bin/ansible-galaxy" collection install \
  -r "${REPO_ROOT}/ansible/requirements.yml" \
  --force

log "checking Ansible"
"${VENV_DIR}/bin/ansible-playbook" --version | sed -n '1,3p'

if [[ "$MODE" == prepare ]]; then
  log "environment prepared; interactive deploy skipped"
  exit 0
fi

exec "${REPO_ROOT}/deploy.sh"
