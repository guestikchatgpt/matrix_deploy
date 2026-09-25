#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO="${MATRIX_DEPLOY_REPO:-guestikchatgpt/matrix_deploy}"
readonly API_ROOT="${MATRIX_DEPLOY_API_ROOT:-https://api.github.com}"
readonly DOWNLOAD_ROOT="${MATRIX_DEPLOY_DOWNLOAD_ROOT:-https://github.com}"
readonly INSTALL_ROOT="${MATRIX_DEPLOY_INSTALL_ROOT:-/opt/matrix-deploy}"
readonly SOURCE_DIR="${INSTALL_ROOT}/source"
readonly RELEASE_MARKER="${INSTALL_ROOT}/installed-release"
readonly CLI_PATH="${MATRIX_DEPLOY_CLI_PATH:-/usr/local/sbin/matrix-deploy}"

log() { printf '[matrix-deploy] %s\n' "$*"; }
fatal() { printf '[matrix-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

[[ ${EUID} -eq 0 ]] || fatal "matrix-deploy update must run as root."
if [[ -d "${SOURCE_DIR}/.git" ]]; then
  fatal "${SOURCE_DIR} is a git clone. Update it with 'git -C ${SOURCE_DIR} pull', then run 'matrix-deploy converge'."
fi

json="$(curl -fsSL --retry 3 --connect-timeout 15 "${API_ROOT}/repos/${REPO}/releases/latest")" \
  || fatal "Unable to resolve latest stable release."
tag="$(printf '%s' "$json" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fatal "Latest release tag is not a stable vX.Y.Z tag: '${tag}'"

current="$(cat "$RELEASE_MARKER" 2>/dev/null || true)"
if [[ "$current" == "$tag" ]]; then
  log "Already on latest stable release: ${tag}"
  exit 0
fi

workdir="$(mktemp -d -t matrix-deploy-update.XXXXXXXX)"
trap 'rm -rf -- "${workdir:-}"' EXIT
archive="${workdir}/matrix-deploy.tar.gz"
manifest="${workdir}/SHA256SUMS"
new_source="${workdir}/source"

curl -fsSL --retry 3 --connect-timeout 15 -o "$archive" \
  "${DOWNLOAD_ROOT}/${REPO}/releases/download/${tag}/matrix-deploy-${tag}.tar.gz" \
  || fatal "Failed to download release archive."
curl -fsSL --retry 3 --connect-timeout 15 -o "$manifest" \
  "${DOWNLOAD_ROOT}/${REPO}/releases/download/${tag}/SHA256SUMS" \
  || fatal "Failed to download SHA256SUMS."

expected="$(awk -v name="matrix-deploy-${tag}.tar.gz" '$2 == name || $2 == "*" name {print $1; exit}' "$manifest")"
actual="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$expected" =~ ^[0-9a-fA-F]{64}$ && "$actual" == "$expected" ]] || fatal "Release archive SHA-256 mismatch."

mkdir -p "$new_source"
tar -xzf "$archive" -C "$new_source" --strip-components=1
[[ -f "$new_source/bootstrap.sh" && -f "$new_source/scripts/matrix-deploy" ]] || fatal "Release bundle is incomplete."

rm -rf "${SOURCE_DIR}.new"
mkdir -p "${SOURCE_DIR}.new"
cp -a "$new_source"/. "${SOURCE_DIR}.new"/
rm -rf "${SOURCE_DIR}.previous"
[[ -d "$SOURCE_DIR" ]] && mv "$SOURCE_DIR" "${SOURCE_DIR}.previous"
mv "${SOURCE_DIR}.new" "$SOURCE_DIR"

# The new tree may pin a different ansible-core/collections; prepare its own
# .venv before declaring the update done, and roll back if that fails.
if ! "${SOURCE_DIR}/bootstrap.sh" --prepare-only; then
  log "Preparing the new release failed; restoring the previous source tree."
  if [[ -d "${SOURCE_DIR}.previous" ]]; then
    rm -rf "$SOURCE_DIR"
    mv "${SOURCE_DIR}.previous" "$SOURCE_DIR"
  fi
  fatal "Update to ${tag} failed; ${current:-previous} release kept."
fi

install -m 0755 "${SOURCE_DIR}/scripts/matrix-deploy" "$CLI_PATH"
printf '%s\n' "$tag" > "$RELEASE_MARKER"
log "Updated installer/playbook: ${current:-unknown} -> ${tag}"
log "Matrix application stack was not changed. Run 'sudo matrix-deploy converge' to apply playbook changes"
log "or 'sudo matrix-deploy upgrade' for an explicit application upgrade."
