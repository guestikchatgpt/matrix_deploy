#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ANSIBLE_DIR="${REPO_ROOT}/ansible"
readonly ANSIBLE_PLAYBOOK="${REPO_ROOT}/.venv/bin/ansible-playbook"
readonly CONFIG_FILE="/etc/matrix-deploy/deployment.yml"
readonly RUNTIME_DIR="/run/matrix-deploy"
readonly SECRET_FILE="${RUNTIME_DIR}/converge-secrets.yml"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run converge.sh as root' >&2; exit 1; }
[[ -x "$ANSIBLE_PLAYBOOK" ]] || { echo 'ERROR: run ./bootstrap.sh first' >&2; exit 1; }
[[ -r "$CONFIG_FILE" ]] || { echo "ERROR: deployment config not found: $CONFIG_FILE" >&2; exit 1; }

EXTRA_VARS=()

cleanup() {
  rm -f "$SECRET_FILE"
}
trap cleanup EXIT INT TERM

case "${1:-}" in
  '')
    ;;
  --admin-password)
    install -d -m 0700 "$RUNTIME_DIR"
    read -r -s -p 'Matrix admin password for initial/recovery creation: ' MATRIX_ADMIN_PASSWORD
    printf '\n'
    [[ -n "$MATRIX_ADMIN_PASSWORD" ]] || { echo 'ERROR: admin password cannot be empty' >&2; exit 1; }
    cat > "$SECRET_FILE" <<EOF_SECRET
---
matrix_admin_password: '${MATRIX_ADMIN_PASSWORD//\'/\'\'}'
EOF_SECRET
    chmod 0600 "$SECRET_FILE"
    unset MATRIX_ADMIN_PASSWORD
    EXTRA_VARS=(--extra-vars "@${SECRET_FILE}")
    ;;
  *)
    echo 'Usage: converge.sh [--admin-password]' >&2
    exit 2
    ;;
esac

cd "$ANSIBLE_DIR"
"$ANSIBLE_PLAYBOOK" playbooks/preflight.yml \
  --extra-vars "@${CONFIG_FILE}" \
  "${EXTRA_VARS[@]}"

"$ANSIBLE_PLAYBOOK" playbooks/site.yml \
  --extra-vars "@${CONFIG_FILE}" \
  "${EXTRA_VARS[@]}"
