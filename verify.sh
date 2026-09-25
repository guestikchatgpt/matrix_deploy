#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ANSIBLE_DIR="${REPO_ROOT}/ansible"
readonly ANSIBLE_PLAYBOOK="${REPO_ROOT}/.venv/bin/ansible-playbook"
readonly CONFIG_FILE="/etc/matrix-deploy/deployment.yml"
readonly VERSION_LOCK_FILE="/etc/matrix-deploy/versions.yml"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run verify.sh as root' >&2; exit 1; }
[[ -x "$ANSIBLE_PLAYBOOK" ]] || { echo 'ERROR: run ./bootstrap.sh first' >&2; exit 1; }
[[ -r "$CONFIG_FILE" ]] || { echo "ERROR: deployment config not found: $CONFIG_FILE" >&2; exit 1; }
[[ -r "$VERSION_LOCK_FILE" ]] || { echo "ERROR: version lock not found: $VERSION_LOCK_FILE" >&2; exit 1; }

cd "$ANSIBLE_DIR"
"$ANSIBLE_PLAYBOOK" playbooks/verify.yml \
  --extra-vars "@${CONFIG_FILE}" \
  --extra-vars "@${VERSION_LOCK_FILE}"

if [[ ${1:-} == '--deep' ]]; then
  "$ANSIBLE_PLAYBOOK" playbooks/verify.yml \
    --extra-vars "@${CONFIG_FILE}" \
    --extra-vars "@${VERSION_LOCK_FILE}" \
    --tags deep_verify
fi
