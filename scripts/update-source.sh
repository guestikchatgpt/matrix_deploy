#!/usr/bin/env bash
set -Eeuo pipefail
readonly REPO="${MATRIX_DEPLOY_REPO:-guestikchatgpt/matrix_deploy}"
readonly API_ROOT="${MATRIX_DEPLOY_API_ROOT:-https://api.github.com}"
readonly INSTALL_ROOT="${MATRIX_DEPLOY_INSTALL_ROOT:-/opt/matrix-deploy}"
readonly SOURCE_DIR="${INSTALL_ROOT}/source"
readonly ETC_DIR="${MATRIX_DEPLOY_ETC_DIR:-/etc/matrix-deploy}"
log(){ printf '[matrix-deploy] %s\n' "$*"; }
fatal(){ printf '[matrix-deploy] ERROR: %s\n' "$*" >&2; exit 1; }
[[ ${EUID} -eq 0 ]] || fatal "matrix-deploy update must run as root."
json="$(curl -fsSL --retry 3 --connect-timeout 15 "${API_ROOT}/repos/${REPO}/releases/latest")" || fatal "Unable to resolve latest stable release."
tag="$(printf '%s' "$json" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[[ -n "$tag" ]] || fatal "Latest release has no tag_name."
current="$(cat "${ETC_DIR}/installed-release" 2>/dev/null || true)"
[[ "$current" == "$tag" ]] && { log "Already on latest stable release: ${tag}"; exit 0; }
workdir="$(mktemp -d -t matrix-deploy-update.XXXXXXXX)"
trap 'rm -rf -- "${workdir:-}"' EXIT INT TERM
archive="${workdir}/matrix-deploy.tar.gz"; manifest="${workdir}/SHA256SUMS"; new_source="${workdir}/source"
curl -fsSL --retry 3 --connect-timeout 15 -o "$archive" "https://github.com/${REPO}/releases/download/${tag}/matrix-deploy-${tag}.tar.gz"
curl -fsSL --retry 3 --connect-timeout 15 -o "$manifest" "https://github.com/${REPO}/releases/download/${tag}/SHA256SUMS"
expected="$(awk '$2 ~ /matrix-deploy.*\.tar\.gz$/ {print $1; exit}' "$manifest")"
actual="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$expected" =~ ^[0-9a-fA-F]{64}$ && "$actual" == "$expected" ]] || fatal "Release archive SHA-256 mismatch."
mkdir -p "$new_source"
tar -xzf "$archive" -C "$new_source" --strip-components=1
[[ -f "$new_source/bootstrap.sh" && -f "$new_source/scripts/matrix-deploy" ]] || fatal "Release bundle is incomplete."
rm -rf "${SOURCE_DIR}.new"; mkdir -p "${SOURCE_DIR}.new"; cp -a "$new_source"/. "${SOURCE_DIR}.new"/
rm -rf "${SOURCE_DIR}.previous"; [[ -d "$SOURCE_DIR" ]] && mv "$SOURCE_DIR" "${SOURCE_DIR}.previous"
mv "${SOURCE_DIR}.new" "$SOURCE_DIR"
install -m 0755 "${SOURCE_DIR}/scripts/matrix-deploy" /usr/local/sbin/matrix-deploy
mkdir -p "$ETC_DIR"; printf '%s\n' "$tag" >"${ETC_DIR}/installed-release"
log "Updated installer/playbook: ${current:-unknown} -> ${tag}"
log "Matrix application stack was not changed. Run 'sudo matrix-deploy upgrade' explicitly for a product upgrade."
