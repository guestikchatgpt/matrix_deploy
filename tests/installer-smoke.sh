#!/usr/bin/env bash
# Functional smoke test for the release installer, the matrix-deploy CLI and
# update-source.sh. It builds a fake GitHub release on the local filesystem and
# serves it through file:// URLs; the bundled bootstrap.sh is replaced by a
# recorder so the test never touches apt, Ansible or the host configuration.
# Must run as root (install.sh and update-source.sh require it).
set -Eeuo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
[[ ${EUID} -eq 0 ]] || { echo 'installer-smoke: run as root (sudo)' >&2; exit 1; }

for f in \
  "$root/install.sh" \
  "$root/bootstrap.sh" \
  "$root/scripts/matrix-deploy" \
  "$root/scripts/update-source.sh"; do
  bash -n "$f"
done

work="$(mktemp -d -t matrix-deploy-smoke.XXXXXXXX)"
trap 'rm -rf -- "$work"' EXIT

repo='example/matrix_deploy'
api="${work}/api"
dl="${work}/dl"
install_root="${work}/opt/matrix-deploy"
cli="${work}/bin/matrix-deploy"
mkdir -p "${work}/bin"

fail() { printf 'installer-smoke: FAIL: %s\n' "$*" >&2; exit 1; }

# publish_release <tag>: build a release bundle from the working tree with a
# recording bootstrap.sh and publish it as the "latest" release.
publish_release() {
  local tag="$1"
  local stage="${work}/stage/matrix-deploy-${tag}"
  rm -rf "${work}/stage"
  mkdir -p "$stage" "${dl}/${repo}/releases/download/${tag}" "${api}/repos/${repo}/releases"
  cp -a "$root/install.sh" "$root/scripts" "$stage/"
  cat > "$stage/bootstrap.sh" <<EOF_BOOT
#!/usr/bin/env bash
printf '%s|%s|%s\n' "\$(cd "\$(dirname "\$0")" && pwd)" "\$*" "\${MATRIX_DEPLOY_RELEASE_TAG:-}" >> '${work}/bootstrap.calls'
EOF_BOOT
  chmod 0755 "$stage/bootstrap.sh"
  tar -C "${work}/stage" -czf "${dl}/${repo}/releases/download/${tag}/matrix-deploy-${tag}.tar.gz" "matrix-deploy-${tag}"
  (cd "${dl}/${repo}/releases/download/${tag}" && sha256sum "matrix-deploy-${tag}.tar.gz" > SHA256SUMS)
  printf '{"tag_name": "%s", "draft": false, "prerelease": false}\n' "$tag" > "${api}/repos/${repo}/releases/latest"
}

export MATRIX_DEPLOY_REPO="$repo"
export MATRIX_DEPLOY_API_ROOT="file://${api}"
export MATRIX_DEPLOY_DOWNLOAD_ROOT="file://${dl}"
export MATRIX_DEPLOY_INSTALL_ROOT="$install_root"
export MATRIX_DEPLOY_CLI_PATH="$cli"
export MATRIX_DEPLOY_NONINTERACTIVE=1

# 1. Fresh install downloads, verifies and hands the extracted tree to
#    bootstrap.sh --install with the resolved release tag.
publish_release v1.0.0
bash "$root/install.sh" >/dev/null
IFS='|' read -r _ args tag < "${work}/bootstrap.calls"
[[ "$args" == '--install' ]] || fail "bootstrap.sh called with '$args', expected --install"
[[ "$tag" == 'v1.0.0' ]] || fail "MATRIX_DEPLOY_RELEASE_TAG='$tag', expected v1.0.0"

# 2. A tampered archive must be rejected before bootstrap runs.
rm -f "${work}/bootstrap.calls"
printf 'tampered' >> "${dl}/${repo}/releases/download/v1.0.0/matrix-deploy-v1.0.0.tar.gz"
if bash "$root/install.sh" >/dev/null 2>&1; then
  fail 'install.sh accepted an archive with a wrong SHA-256'
fi
[[ ! -e "${work}/bootstrap.calls" ]] || fail 'bootstrap.sh ran despite checksum mismatch'

# 3. Prerelease-shaped tags are refused.
printf '{"tag_name": "v1.1.0-rc.1"}\n' > "${api}/repos/${repo}/releases/latest"
if bash "$root/install.sh" >/dev/null 2>&1; then
  fail 'install.sh accepted a non-stable release tag'
fi

# 4. The CLI dispatches to the lifecycle scripts of the installed tree.
src="${install_root}/source"
mkdir -p "$src/scripts"
for s in converge.sh upgrade.sh verify.sh check.sh backup.sh destroy.sh; do
  printf '#!/usr/bin/env bash\necho "%s $*"\n' "$s" > "$src/$s"
  chmod 0755 "$src/$s"
done
cp "$root/scripts/matrix-deploy" "$cli"
chmod 0755 "$cli"
[[ "$("$cli" verify --deep)" == 'verify.sh --deep' ]] || fail 'matrix-deploy verify did not dispatch to verify.sh'
[[ "$("$cli" upgrade)" == 'upgrade.sh ' ]] || fail 'matrix-deploy upgrade did not dispatch to upgrade.sh'
[[ "$("$cli" backup --include-media)" == 'backup.sh --include-media' ]] || fail 'matrix-deploy backup did not dispatch'
printf 'v1.0.0\n' > "${install_root}/installed-release"
[[ "$("$cli" version)" == 'v1.0.0' ]] || fail 'matrix-deploy version'
if "$cli" bogus >/dev/null 2>&1; then fail 'unknown command accepted'; fi

# 5. update-source.sh swaps the source tree, prepares it and records the tag.
cp -a "$root/scripts/update-source.sh" "$src/scripts/"
publish_release v1.1.0
rm -f "${work}/bootstrap.calls"
bash "$src/scripts/update-source.sh" >/dev/null
[[ "$(cat "${install_root}/installed-release")" == 'v1.1.0' ]] || fail 'update did not record the new release'
[[ -d "${src}.previous" ]] || fail 'update did not keep the previous source tree'
IFS='|' read -r dir args _ < "${work}/bootstrap.calls"
[[ "$dir" == "$src" && "$args" == '--prepare-only' ]] || fail "update ran '$dir/bootstrap.sh $args'"
second_update="$(bash "$src/scripts/update-source.sh")"
[[ "$second_update" == *'Already on latest'* ]] || fail 'second update was not a no-op'

printf 'installer smoke tests: OK\n'
