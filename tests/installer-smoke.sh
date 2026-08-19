#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
for f in \
  "$root/install.sh" \
  "$root/bootstrap.sh" \
  "$root/scripts/matrix-deploy" \
  "$root/scripts/update-source.sh" \
  "$root/scripts/upgrade-stack.sh"; do
  bash -n "$f"
done
grep -q 'releases/latest' "$root/install.sh"
grep -q 'SHA256SUMS' "$root/install.sh"
grep -q 'SOURCE_DIR="${INSTALL_ROOT}/source"' "$root/bootstrap.sh"
grep -q '/usr/local/sbin/matrix-deploy' "$root/bootstrap.sh"
grep -q "was not changed" "$root/scripts/update-source.sh"
grep -q 'Pre-upgrade backup' "$root/scripts/upgrade-stack.sh"
printf 'installer smoke tests: OK\n'
