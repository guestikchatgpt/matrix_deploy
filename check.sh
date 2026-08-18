#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ANSIBLE_DIR="${REPO_ROOT}/ansible"
readonly VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
readonly ANSIBLE_PLAYBOOK="${REPO_ROOT}/.venv/bin/ansible-playbook"
readonly CONFIG_FILE="/etc/matrix-deploy/deployment.yml"
readonly MATRIX_ROOT="/opt/matrix"
readonly SECRET_DIR="${MATRIX_ROOT}/.secrets"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run check.sh as root' >&2; exit 1; }
[[ -x "$ANSIBLE_PLAYBOOK" && -x "$VENV_PYTHON" ]] || { echo 'ERROR: run ./bootstrap.sh --prepare-only first' >&2; exit 1; }
[[ -r "$CONFIG_FILE" ]] || { echo "ERROR: deployment config not found: $CONFIG_FILE" >&2; exit 1; }
[[ -d "$MATRIX_ROOT" ]] || { echo 'ERROR: check.sh is for an existing deployed Matrix installation; use ./bootstrap.sh for a fresh host' >&2; exit 1; }

readarray -t MATRIX_CERT_NAMES < <("$VENV_PYTHON" - "$CONFIG_FILE" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding='utf-8')) or {}
base = cfg.get('matrix_base_domain', '')
prefixes = cfg.get('matrix_subdomains', {}) or {}
if not base:
    raise SystemExit('matrix_base_domain missing from deployment config')
print(f"{prefixes.get('synapse', 'matrix')}.{base}")
print(f"{prefixes.get('turn', 'turn')}.{base}")
PY
)

MATRIX_SERVER_NAME="${MATRIX_CERT_NAMES[0]:-}"
TURN_SERVER_NAME="${MATRIX_CERT_NAMES[1]:-}"
[[ -n "$MATRIX_SERVER_NAME" && -n "$TURN_SERVER_NAME" ]] || { echo 'ERROR: could not derive certificate lineages from deployment config' >&2; exit 1; }

required_state=(
  "${SECRET_DIR}/turn_shared_secret"
  "${SECRET_DIR}/livekit_api_key"
  "${SECRET_DIR}/livekit_api_secret"
  "${SECRET_DIR}/postgres_synapse_password"
  "${SECRET_DIR}/synapse_registration_shared_secret"
  "${SECRET_DIR}/synapse_macaroon_secret"
  "${SECRET_DIR}/synapse_form_secret"
  "${SECRET_DIR}/coturn_cli"
  "/etc/letsencrypt/live/${MATRIX_SERVER_NAME}/fullchain.pem"
  "/etc/letsencrypt/live/${MATRIX_SERVER_NAME}/privkey.pem"
  "/etc/letsencrypt/live/${TURN_SERVER_NAME}/fullchain.pem"
  "/etc/letsencrypt/live/${TURN_SERVER_NAME}/privkey.pem"
)

for path in "${required_state[@]}"; do
  [[ -r "$path" ]] || {
    echo "ERROR: required managed state is missing: $path" >&2
    echo 'Run converge/recovery first; check.sh deliberately refuses to generate missing secrets or certificates.' >&2
    exit 1
  }
done

cd "$ANSIBLE_DIR"
"$ANSIBLE_PLAYBOOK" playbooks/preflight.yml --extra-vars "@${CONFIG_FILE}"
exec "$ANSIBLE_PLAYBOOK" playbooks/site.yml \
  --extra-vars "@${CONFIG_FILE}" \
  --check \
  --diff
