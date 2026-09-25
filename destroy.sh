#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ANSIBLE_DIR="${REPO_ROOT}/ansible"
readonly ANSIBLE_PLAYBOOK="${REPO_ROOT}/.venv/bin/ansible-playbook"
readonly VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
readonly CONFIG_FILE="/etc/matrix-deploy/deployment.yml"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run destroy.sh as root' >&2; exit 1; }
[[ -x "$ANSIBLE_PLAYBOOK" ]] || { echo 'ERROR: run ./bootstrap.sh first' >&2; exit 1; }
[[ -r "$CONFIG_FILE" ]] || { echo "ERROR: deployment config not found: $CONFIG_FILE" >&2; exit 1; }

server_name="$("$VENV_PYTHON" - "$CONFIG_FILE" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
base = cfg.get('matrix_base_domain', '')
prefixes = cfg.get('matrix_subdomains', {}) or {}
print(f"{prefixes.get('synapse', 'matrix')}.{base}")
PY
)"

printf 'DANGER: this removes Matrix containers, database/data, Matrix Nginx/Coturn state, Matrix certificates and Matrix-specific firewall rules.\n'
printf 'Backups and system packages are preserved.\n\n'
printf 'A fresh backup including the Synapse media store will be created first.\n'
read -r -p "Type BACKUP ${server_name} to continue: " backup_confirm
[[ "$backup_confirm" == "BACKUP ${server_name}" ]] || { echo 'Destroy cancelled.'; exit 1; }

"${REPO_ROOT}/backup.sh" --include-media

printf '\nFull backup completed. Destruction is now irreversible without a tested restore procedure.\n'
read -r -p "Type DESTROY ${server_name} to remove the deployment: " destroy_confirm
[[ "$destroy_confirm" == "DESTROY ${server_name}" ]] || { echo 'Destroy cancelled after backup; no Matrix state was removed.'; exit 1; }

cd "$ANSIBLE_DIR"
exec "$ANSIBLE_PLAYBOOK" playbooks/destroy.yml --extra-vars "@${CONFIG_FILE}"
