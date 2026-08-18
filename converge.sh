#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ANSIBLE_DIR="${REPO_ROOT}/ansible"
readonly ANSIBLE_PLAYBOOK="${REPO_ROOT}/.venv/bin/ansible-playbook"
readonly CONFIG_FILE="/etc/matrix-deploy/deployment.yml"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run converge.sh as root' >&2; exit 1; }
[[ -x "$ANSIBLE_PLAYBOOK" ]] || { echo 'ERROR: run ./bootstrap.sh first' >&2; exit 1; }
[[ -r "$CONFIG_FILE" ]] || { echo "ERROR: deployment config not found: $CONFIG_FILE" >&2; exit 1; }

cd "$ANSIBLE_DIR"
"$ANSIBLE_PLAYBOOK" playbooks/preflight.yml --extra-vars "@${CONFIG_FILE}"
exec "$ANSIBLE_PLAYBOOK" playbooks/site.yml --extra-vars "@${CONFIG_FILE}"
