# One-command installation

**English** | [Русский](ru/INSTALLER.md)

Step-by-step installation from a git clone as root is described in
[`INSTALL.md`](INSTALL.md).

The distribution channel is GitHub Releases
(<https://github.com/guestikchatgpt/matrix_deploy/releases>).

## First installation

```bash
curl -fsSL https://raw.githubusercontent.com/guestikchatgpt/matrix_deploy/main/install.sh | sudo bash
```

`install.sh`:

1. finds the latest stable release via `/releases/latest` and accepts only
   tags of the form `vX.Y.Z`;
2. downloads `matrix-deploy-<tag>.tar.gz` and `SHA256SUMS` and verifies the
   SHA-256 of that exact archive;
3. runs `bootstrap.sh --install` from the unpacked release.

`bootstrap.sh --install` copies the source tree to `/opt/matrix-deploy/source`
(the previous version is kept in `source.previous`), writes the tag to
`/opt/matrix-deploy/installed-release`, installs the
`/usr/local/sbin/matrix-deploy` CLI and continues the regular `bootstrap.sh`
from the installed copy: venv, pinned ansible-core and collections, then the
interactive `deploy.sh`.

For automated host preparation without the interactive deployment:

```bash
curl -fsSL .../install.sh | sudo MATRIX_DEPLOY_NONINTERACTIVE=1 bash
# later, interactively:
sudo /opt/matrix-deploy/source/bootstrap.sh
```

Running `deploy.sh`/`bootstrap.sh` again on top of an existing installation
(`/etc/matrix-deploy/deployment.yml`) is deliberately refused: it would
overwrite the topology and upgrade versions without a backup. For an existing
installation use the commands below.

## After installation

```bash
sudo matrix-deploy converge            # re-apply the configuration with the current version lock
sudo matrix-deploy converge --admin-password   # resume an interrupted first installation
sudo matrix-deploy upgrade             # backup -> refresh version lock -> converge
sudo matrix-deploy verify [--deep]     # check the stack (--deep: certbot renew --dry-run)
sudo matrix-deploy check               # ansible --check --diff
sudo matrix-deploy backup [--include-media]
sudo matrix-deploy destroy             # with a backup and two confirmations
sudo matrix-deploy update              # update the installer itself to the latest release
sudo matrix-deploy version
```

`update` only changes the installer/playbook sources: it downloads and
verifies the new release, prepares its `.venv` (`bootstrap.sh --prepare-only`)
and rolls back to the previous version on failure. The Matrix stack is not
touched — playbook changes are applied by an explicit `matrix-deploy converge`,
application upgrades by an explicit `matrix-deploy upgrade`.

Installer state (`/opt/matrix-deploy/installed-release`) is stored separately
from deployment state (`/etc/matrix-deploy`), so `destroy` does not lose track
of the installed installer version.

## Publishing a release

Pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`: a `git
archive` of the tagged tree with a single top-level directory, plus
`SHA256SUMS`, are published as assets of a non-prerelease release.

## Tests

`tests/installer-smoke.sh` (run as root; in CI via `sudo`) builds a fake
release on the local filesystem and, via `file://`, checks: installation,
refusal on a wrong SHA-256 and on a prerelease tag, CLI dispatch, and
`update-source.sh` preparing a new tree.
