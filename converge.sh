#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ANSIBLE_DIR="${REPO_ROOT}/ansible"
readonly ANSIBLE_PLAYBOOK="${REPO_ROOT}/.venv/bin/ansible-playbook"
readonly CONFIG_FILE="/etc/matrix-deploy/deployment.yml"
readonly VERSION_LOCK_FILE="/etc/matrix-deploy/versions.yml"
readonly RUNTIME_DIR="/run/matrix-deploy"
readonly SECRET_FILE="${RUNTIME_DIR}/converge-secrets.yml"

[[ ${EUID} -eq 0 ]] || { echo 'ERROR: run converge.sh as root' >&2; exit 1; }
[[ -x "$ANSIBLE_PLAYBOOK" ]] || { echo 'ERROR: run ./bootstrap.sh first' >&2; exit 1; }
[[ -r "$CONFIG_FILE" ]] || { echo "ERROR: deployment config not found: $CONFIG_FILE" >&2; exit 1; }

ASK_ADMIN_PASSWORD=false
REFRESH_VERSIONS=false

while (( $# > 0 )); do
  case "$1" in
    --admin-password)
      ASK_ADMIN_PASSWORD=true
      ;;
    --refresh-versions)
      REFRESH_VERSIONS=true
      ;;
    -h|--help)
      echo 'Usage: converge.sh [--admin-password] [--refresh-versions]'
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      echo 'Usage: converge.sh [--admin-password] [--refresh-versions]' >&2
      exit 2
      ;;
  esac
  shift
done

# Existing deployments created before the dynamic lock mechanism do not have a
# versions.yml yet. Bootstrap it once from current upstream stable releases.
if [[ ! -r "$VERSION_LOCK_FILE" ]]; then
  REFRESH_VERSIONS=true
fi

EXTRA_VARS=()

cleanup() {
  rm -f "$SECRET_FILE"
}
trap cleanup EXIT INT TERM

if [[ "$ASK_ADMIN_PASSWORD" == true ]]; then
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
fi

PREFLIGHT_ARGS=(
  playbooks/preflight.yml
  --extra-vars "@${CONFIG_FILE}"
)

if [[ "$REFRESH_VERSIONS" == true ]]; then
  PREFLIGHT_ARGS+=(--extra-vars 'matrix_refresh_versions=true')
fi

cd "$ANSIBLE_DIR"
"$ANSIBLE_PLAYBOOK" "${PREFLIGHT_ARGS[@]}" "${EXTRA_VARS[@]}"

[[ -r "$VERSION_LOCK_FILE" ]] || {
  echo "ERROR: version lock not found after preflight: $VERSION_LOCK_FILE" >&2
  exit 1
}

"$ANSIBLE_PLAYBOOK" playbooks/site.yml \
  --extra-vars "@${CONFIG_FILE}" \
  --extra-vars "@${VERSION_LOCK_FILE}" \
  "${EXTRA_VARS[@]}"
