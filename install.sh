#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO="${MATRIX_DEPLOY_REPO:-guestikchatgpt/matrix_deploy}"
readonly API_ROOT="${MATRIX_DEPLOY_API_ROOT:-https://api.github.com}"
readonly INSTALL_ROOT="${MATRIX_DEPLOY_INSTALL_ROOT:-/opt/matrix-deploy}"

log() { printf '[matrix-deploy] %s\n' "$*"; }
fatal() { printf '[matrix-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

need_root() {
  [[ ${EUID} -eq 0 ]] || fatal "Run as root (for example: curl ... | sudo bash)."
}

ensure_bootstrap_tools() {
  local missing=() tool
  for tool in curl tar sha256sum awk sed grep mktemp; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  ((${#missing[@]} == 0)) && return 0
  command -v apt-get >/dev/null 2>&1 || fatal "Missing tools: ${missing[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl tar coreutils gawk sed grep
}

fetch_latest_release() {
  local api="${API_ROOT}/repos/${REPO}/releases/latest"
  local json
  json="$(curl -fsSL --retry 3 --connect-timeout 15 "$api")" || fatal "Unable to resolve latest stable GitHub Release."
  RELEASE_TAG="$(printf '%s' "$json" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  [[ -n "$RELEASE_TAG" ]] || fatal "Latest release has no tag_name."
}

fetch_release_asset() {
  local name="$1" out="$2"
  local url="https://github.com/${REPO}/releases/download/${RELEASE_TAG}/${name}"
  curl -fsSL --retry 3 --connect-timeout 15 -o "$out" "$url" || fatal "Failed to download release asset: ${name}"
}

verify_checksum() {
  local archive="$1" manifest="$2"
  local expected actual
  expected="$(awk '$2 ~ /matrix-deploy.*\.tar\.gz$/ {print $1; exit}' "$manifest")"
  [[ "$expected" =~ ^[0-9a-fA-F]{64}$ ]] || fatal "Checksum manifest does not contain a valid SHA-256 for the release archive."
  actual="$(sha256sum "$archive" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || fatal "Release archive SHA-256 mismatch."
}

main() {
  need_root
  ensure_bootstrap_tools

  if [[ "${MATRIX_DEPLOY_NONINTERACTIVE:-0}" != "1" ]]; then
    [[ -r /dev/tty && -w /dev/tty ]] || fatal "Interactive terminal required; curl | sudo bash is supported."
  fi

  fetch_latest_release
  log "Resolved latest stable release: ${RELEASE_TAG}"

  local workdir archive manifest source
  workdir="$(mktemp -d -t matrix-deploy.XXXXXXXX)"
  trap 'rm -rf -- "${workdir:-}"' EXIT INT TERM
  archive="${workdir}/matrix-deploy.tar.gz"
  manifest="${workdir}/SHA256SUMS"
  source="${workdir}/source"
  mkdir -p "$source"

  fetch_release_asset "matrix-deploy-${RELEASE_TAG}.tar.gz" "$archive"
  fetch_release_asset "SHA256SUMS" "$manifest"
  verify_checksum "$archive" "$manifest"
  log "Release checksum verified"

  tar -xzf "$archive" -C "$source" --strip-components=1
  [[ -x "$source/bootstrap.sh" || -f "$source/bootstrap.sh" ]] || fatal "Release bundle does not contain bootstrap.sh."

  export MATRIX_DEPLOY_RELEASE_TAG="$RELEASE_TAG"
  export MATRIX_DEPLOY_INSTALL_ROOT="$INSTALL_ROOT"

  if [[ "${MATRIX_DEPLOY_NONINTERACTIVE:-0}" == "1" ]]; then
    bash "$source/bootstrap.sh" install "$@"
  else
    bash "$source/bootstrap.sh" install "$@" </dev/tty
  fi
}

main "$@"
