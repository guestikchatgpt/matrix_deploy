# Server installation: step by step as root

**English** | [Русский](ru/INSTALL.md)

This guide is for a clean **Ubuntu 24.04 LTS (noble)** server where you work
as `root`. Ubuntu 26.04 is not supported yet: `bootstrap.sh` and preflight
refuse to run on anything other than 24.04.

The model is Ansible with a local controller: everything runs on the server
itself, no separate Ansible machine is needed.

## 0. Prepare in advance

**Server**

- clean Ubuntu 24.04 LTS, `root` access (or `sudo -i`);
- at least 2 vCPU and 2 GiB RAM (4 GiB or more recommended), at least
  10 GiB of free space on `/`;
- a public IPv4 address: assigned directly to the server or behind NAT with
  port forwarding.

**DNS — do this before running the installer.** Create six A records at your
DNS provider pointing to the server's public IPv4 (prefixes can be changed
during installation; below are the defaults for `example.com`). Installation
will not start without them: preflight checks every record, and Let's Encrypt
certificates can only be issued for names that already point to the server.
New records take anywhere from a few minutes to a couple of hours to
propagate, so create them ahead of time.

| Name | Purpose |
| --- | --- |
| `matrix.example.com` | Synapse (also the Matrix server name: `@user:matrix.example.com`) |
| `element.example.com` | Element Web |
| `synad.example.com` | Ketesa (Synapse admin panel) |
| `call.example.com` | Element Call |
| `rtc.example.com` | LiveKit / MatrixRTC |
| `turn.example.com` | classic TURN (Coturn) |

Each name must have exactly one A record — the IP of this server.
**There must be no AAAA records**: IPv6 mode is not supported, and preflight
stops the installation if it finds AAAA records.

**Ports.** If there is a cloud firewall or NAT in front of the server, open or
forward these inbound ports (the installer configures UFW on the server
itself):

| Port | Purpose |
| --- | --- |
| TCP 22 (or your SSH port) | SSH |
| TCP 80, 443 | HTTP/ACME, HTTPS |
| TCP 8448 | federation (if enabled) |
| TCP+UDP 3478, 5349 | Coturn |
| UDP 57000-57999 | Coturn relay |
| TCP 7881, UDP 62000-62999 | LiveKit RTC |
| UDP 3480, TCP 5449 | LiveKit embedded TURN |
| UDP 63000-63999 | LiveKit embedded TURN relay |

You will also need an email address for Let's Encrypt and a password for the
future Matrix administrator (`@admin`).

## 1. Connect to the server

```bash
ssh root@<server-IP>
```

It is best to run the installation inside `tmux` so that a dropped SSH
connection does not interrupt Ansible:

```bash
apt-get update && apt-get install -y tmux
tmux new -s matrix
```

To get back into the session after a disconnect: `tmux attach -t matrix`.

## 2. Update the system

```bash
apt-get update
apt-get -y full-upgrade
[ -f /var/run/reboot-required ] && reboot
```

If the server rebooted, reconnect (and reopen `tmux`).

## 3. Install git

```bash
apt-get install -y git ca-certificates
```

**Do not install Ansible manually — there is no need.** `bootstrap.sh` installs
the Python dependencies, `skopeo`, `dnsutils` and the rest itself, creates the
`.venv` virtual environment and installs the pinned ansible-core 2.21.4 and
the collections from `ansible/requirements.yml` into it. The `ansible` apt
package on Ubuntu 24.04 is too old and is not used.

## 4. Clone the repository

Clone into `/opt/matrix-deploy/source` — this is the standard location the
`matrix-deploy` CLI works with:

```bash
mkdir -p /opt/matrix-deploy
git clone https://github.com/guestikchatgpt/matrix_deploy.git /opt/matrix-deploy/source
cd /opt/matrix-deploy/source
git log --oneline -1   # make sure this is main and the expected commit
```

To install a specific release instead of the current `main`, run
`git -C /opt/matrix-deploy/source checkout v1.0.1` after cloning. Instead of
steps 3–4 and 6 you can install a release with a single command — see
[`INSTALLER.md`](INSTALLER.md).

## 5. Check DNS before starting

```bash
for h in matrix element synad call rtc turn; do
  printf '%-8s A=%s AAAA=%s\n' "$h" \
    "$(dig +short A $h.example.com | tr '\n' ' ')" \
    "$(dig +short AAAA $h.example.com | tr '\n' ' ')"
done
```

(`dig` becomes available after step 6; before that you can install it
manually: `apt-get install -y dnsutils`.) Every name should show only your
IPv4, and AAAA should be empty.

## 6. Run the installation

```bash
cd /opt/matrix-deploy/source
./bootstrap.sh --install
```

What happens:

1. `--install` installs the `/usr/local/sbin/matrix-deploy` CLI and records
   the current commit in `/opt/matrix-deploy/installed-release`. The
   repository stays in place — nothing is copied, because it already lives in
   `/opt/matrix-deploy/source`.
2. `bootstrap.sh` installs system dependencies, `.venv`, ansible-core and the
   collections.
3. The interactive `deploy.sh` starts.

`deploy.sh` questions (the default is in brackets; Enter accepts it):

| Question | What to answer |
| --- | --- |
| Base domain | your domain, e.g. `example.com` |
| Matrix/Element/Ketesa/Call/RTC/TURN prefixes | Enter, if DNS was set up per the table above |
| Let's Encrypt email | a working email address |
| Enable Matrix federation? | `y`: talk to other Matrix servers; `n`: closed server |
| Use this IPv4? | make sure the detected IP really is the server's public IP |
| Local relay IP for Coturn (NAT only) | the server's internal IP that NAT forwards the ports to |
| Matrix admin password | password for `@admin:matrix.<domain>` (not echoed) |

Then the installer:

- saves the topology to `/etc/matrix-deploy/deployment.yml`;
- runs preflight: OS, resources, DNS A/AAAA, free ports, apt, and selection of
  the latest stable versions with image checks against the registry;
- pins the versions in `/etc/matrix-deploy/versions.yml`;
- shows the plan and asks **“Preflight succeeded. Start the deployment?”**.
  The default answer is “no”; type `y` to continue.

The main playbook takes a few minutes: Docker, Nginx and Let's Encrypt
certificates, PostgreSQL, Synapse, web applications, LiveKit, Coturn, UFW,
Fail2ban. At the end the verifier runs automatically; a successful
installation ends with the line `All required checks passed.`

## 7. Check the result

```bash
matrix-deploy verify
```

- Element Web: `https://element.<domain>`, log in as `admin` with the password
  from step 6;
- Ketesa admin panel: `https://synad.<domain>`, log in as the same `@admin`;
- Element Call: `https://call.<domain>`.

## Troubleshooting

- **Preflight complains about DNS, AAAA records or busy ports** — fix the cause
  and run `matrix-deploy converge --admin-password` (the topology is already
  saved; you do not need to enter it again).
- **The installation was interrupted halfway** (SSH dropped, network error) —
  `matrix-deploy converge --admin-password`. The password is only needed if
  the `@admin` account has not been created yet; it is not stored in
  `/etc/matrix-deploy`.
- **An image fails to download** (`i/o timeout` to `ghcr.io` or Docker Hub).
  The installer itself tries upstream, the official alternative registry and
  public mirrors, always by the pinned digest. If none of them work, add your
  own mirror or proxy to `/etc/matrix-deploy/deployment.yml`, for example:

  ```yaml
  matrix_registry_proxy: "http://user:password@proxy.example:3128"
  # and/or your own mirrors (they fully replace the default list):
  matrix_registry_mirrors:
    docker.io: [mirror.gcr.io, dockerhub.timeweb.cloud]
    ghcr.io: [ghcr.nju.edu.cn, ghcr.m.daocloud.io]
  ```

  and run `matrix-deploy converge --admin-password`.
- **PostgreSQL did not come up on the first install** (`Wait for PostgreSQL ...
  Connection refused`, no `postgres` container in `docker ps`). In versions
  before the first-start fix, the container was restarted in the middle of
  database initialization and left it half-done. As long as Synapse has never
  started, the database holds no data and can be safely recreated:

  ```bash
  docker logs --tail 50 postgres        # see the cause
  docker rm -f postgres
  rm -rf /opt/matrix/postgres/data/pgdata
  git -C /opt/matrix-deploy/source pull
  matrix-deploy converge --admin-password
  ```

  **Never** do this on a running installation with users — it deletes the
  database.
- **Running `./bootstrap.sh` again** on an already configured server is
  deliberately refused: it would overwrite the topology and upgrade versions
  without a backup. Use `matrix-deploy converge`.
- **Starting from scratch** — `matrix-deploy destroy` (it takes a full backup
  first and asks for confirmation twice), then step 6 again.

## Day-to-day operations

```bash
matrix-deploy verify              # check the stack; --deep adds certbot renew --dry-run
matrix-deploy converge            # re-apply the configuration (versions unchanged)
matrix-deploy upgrade             # backup -> latest stable versions -> apply
matrix-deploy backup              # back up the DB and configuration (--include-media adds media)
matrix-deploy check               # ansible --check --diff, no changes
matrix-deploy version             # installed installer version
```

**Updating the playbook itself** for a git installation:

```bash
git -C /opt/matrix-deploy/source pull
cd /opt/matrix-deploy/source && ./bootstrap.sh --prepare-only   # if ansible/collection pins changed
matrix-deploy converge
```

`matrix-deploy update` is meant for installations from a GitHub Release and
deliberately refuses to run on a git clone, so that the clone is not replaced
by an archive.

If, after updating the playbook, `converge` reports that the locked versions
are older than supported (`locked versions are older than this playbook
supports`), run `matrix-deploy upgrade`: it takes a backup and upgrades the
versions.
