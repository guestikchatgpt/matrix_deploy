# One-command installer

The distribution contract is GitHub Releases. The repository can remain private during development, but anonymous one-line installation requires the repository/release assets to be public.

Fresh install after the first stable release:

```bash
curl -fsSL https://raw.githubusercontent.com/guestikchatgpt/matrix_deploy/main/install.sh | sudo bash
```

`install.sh` resolves the latest non-prerelease GitHub Release through `/releases/latest`, downloads `matrix-deploy-<tag>.tar.gz` plus `SHA256SUMS`, verifies SHA-256, installs the source tree under `/opt/matrix-deploy/source`, and runs the interactive Ansible bootstrap. Persistent installer state lives under `/etc/matrix-deploy`.

After installation:

```bash
sudo matrix-deploy update
sudo matrix-deploy upgrade
sudo matrix-deploy verify
sudo matrix-deploy version
```

`update` updates only the installer/playbook source to the latest stable release. It does not silently update the Matrix application stack. `upgrade` is the explicit application-stack upgrade path; it creates a pre-upgrade backup first and remains fail-closed until the dynamic version resolver is implemented.
