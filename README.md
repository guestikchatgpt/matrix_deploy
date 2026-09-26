# Matrix Deploy

**English** | [Русский](README.ru.md)

[![Release](https://img.shields.io/github/v/release/guestikchatgpt/matrix_deploy)](https://github.com/guestikchatgpt/matrix_deploy/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Current release: [v1.0.1](https://github.com/guestikchatgpt/matrix_deploy/releases/tag/v1.0.1).**

Deploys your own [Matrix](https://matrix.org) messenger — with a web client,
an admin panel, and audio and video calls — on a single clean **Ubuntu 24.04
LTS** server. Installation is interactive: you answer a few questions (domain,
email, admin password) and everything else happens automatically, including
TLS certificates, the firewall and a final self-check.

## What you get

| Address | What it is |
| --- | --- |
| `https://element.<domain>` | **Element Web** — web client for chats and calls |
| `https://call.<domain>` | **Element Call** — standalone video-calling app |
| `https://synad.<domain>` | **Ketesa** — server admin panel (users, rooms, media) |
| `matrix.<domain>` | **Synapse** — the Matrix server itself; user IDs look like `@user:matrix.<domain>` |

Two more addresses are internal: nobody opens them in a browser, but calls do
not work without them, and their DNS records are required.

| Address | What it is |
| --- | --- |
| `rtc.<domain>` | **LiveKit** and the call authorization service — group calls from Element Call and Element X go through it |
| `turn.<domain>` | **Coturn** (TURN) — helps 1:1 calls get through NAT and firewalls |

Mobile clients (Element X and others) can connect too — just point them at
`matrix.<domain>`. The first administrator is `@admin`; its password is set
during installation. Federation with other Matrix servers can be switched on
or off.

## How it works

- **Ansible runs on the server itself.** The repository is placed on the
  server and run there; no separate Ansible control machine is needed. The
  required Ansible versions are installed automatically into an isolated
  environment.
- **Applications in Docker:** PostgreSQL, Synapse, Element Web, Element Call,
  Ketesa, LiveKit and the call authorization service `lk-jwt-service`.
- **On the host:** Nginx (a single HTTPS entry point) with automatic Let's
  Encrypt certificates, Coturn, the UFW firewall, and SSH protection via
  Fail2ban.
- **Calls:** Element Call group calls go through LiveKit, classic 1:1 calls go
  through Coturn.
- **Versions:** installation picks the latest stable releases (no beta/rc) and
  pins them. Re-applying the configuration never changes versions; upgrades
  happen only through the explicit `upgrade` command, which always takes a
  backup first.
- **Resilient image pulls:** if GitHub or Docker Hub is unreachable, images are
  pulled from alternative registries and public mirrors — strictly by the
  pinned digest, so they cannot be substituted. You can also set your own
  proxy.
- **Self-check:** after installation, and on the `verify` command, services,
  containers, certificates, the firewall and endpoints are checked.

Implementation details are in the [technical documentation](docs/TECHNICAL.md).

## Requirements

- a clean **Ubuntu 24.04 LTS** server (other versions, including 26.04, are not
  supported yet) with `root` access;
- at least 2 vCPU and 2 GiB RAM (4 GiB recommended), at least 10 GiB of free
  disk space;
- a public IPv4 address (direct, or behind NAT with port forwarding);
- a domain with six DNS A records **created in advance** pointing to the
  server's IP: `matrix`, `element`, `synad`, `call`, `rtc`, `turn` (no AAAA
  records) — see [“DNS first”](#dns-first-before-running-the-installer);
- open inbound ports — listed in the [installation guide](docs/INSTALL.md#0-prepare-in-advance).

## Installation

### DNS first (before running the installer!)

Before you start, create **six A records** at your DNS provider, all pointing
to the server's public IPv4. Example for the domain `example.com` and the
server `203.0.113.10`:

| Name | Type | Value |
| --- | --- | --- |
| `matrix.example.com` | A | `203.0.113.10` |
| `element.example.com` | A | `203.0.113.10` |
| `synad.example.com` | A | `203.0.113.10` |
| `call.example.com` | A | `203.0.113.10` |
| `rtc.example.com` | A | `203.0.113.10` |
| `turn.example.com` | A | `203.0.113.10` |

Why in advance:

- the installer checks these records at the very beginning and **stops** if
  any of them is missing or points elsewhere;
- during installation, Let's Encrypt certificates are issued for all of these
  names, which is only possible once DNS already points to the server.

There must be no AAAA (IPv6) records for these names. New records can take
anywhere from a few minutes to a couple of hours to propagate; check with
`dig +short A matrix.example.com` — it should return the server's IP. You can
choose other prefixes instead of `matrix`, `element`, etc. during installation;
in that case create the records with those prefixes.

### Then — the installation itself

As `root`, make sure `curl` is available, then run the installer. It downloads
the latest stable GitHub Release and verifies the archive's SHA-256 before
running it:

```bash
apt-get update && apt-get install -y ca-certificates curl
curl -fsSL https://raw.githubusercontent.com/guestikchatgpt/matrix_deploy/main/install.sh -o /root/matrix-deploy-install.sh && bash /root/matrix-deploy-install.sh
```

Or clone a specific release tag (also as `root`):

```bash
apt-get update && apt-get install -y git
mkdir -p /opt/matrix-deploy
git clone --branch v1.0.1 https://github.com/guestikchatgpt/matrix_deploy.git /opt/matrix-deploy/source
cd /opt/matrix-deploy/source
./bootstrap.sh --install
```

The installer asks its questions, checks the server and DNS, shows the plan
and, once you confirm, deploys everything. **For a step-by-step guide with DNS
and port preparation and troubleshooting, see [`docs/INSTALL.md`](docs/INSTALL.md).**

## Commands

After installation, everything is managed with the `matrix-deploy` command
(as `root`):

| Command | What it does |
| --- | --- |
| `matrix-deploy verify` | check that everything works (`--deep` also tests certificate renewal) |
| `matrix-deploy converge` | re-apply the configuration (application versions stay the same) |
| `matrix-deploy converge --admin-password` | resume an interrupted first installation |
| `matrix-deploy upgrade` | upgrade applications to the latest stable versions (backup first) |
| `matrix-deploy backup` | back up the database and configuration (`--include-media` also includes user files) |
| `matrix-deploy check` | show what would change, without changing anything |
| `matrix-deploy destroy` | remove the installation (full backup first, two confirmations) |
| `matrix-deploy update` | update the installer from a GitHub Release; for a git clone use the commands below |
| `matrix-deploy version` | installer version |

The same operations are available as scripts in the repository root:
`converge.sh`, `upgrade.sh`, `verify.sh`, `backup.sh`, `check.sh`, `destroy.sh`.

If you installed from a git clone of a release tag (e.g. `v1.0.1`), update it by checking
out the next tag you want. Such a clone is in detached HEAD, so a plain
`git pull` will not work:

```bash
git -C /opt/matrix-deploy/source fetch --tags
git -C /opt/matrix-deploy/source checkout vX.Y.Z  # use the new published tag
/opt/matrix-deploy/source/bootstrap.sh --prepare-only
matrix-deploy converge
```

If `converge` reports that the new playbooks require newer application
versions, run `matrix-deploy upgrade` (it takes a backup before upgrading).

## Files

**In the repository**

| Path | Purpose |
| --- | --- |
| `bootstrap.sh` | prepares the server and starts the installation |
| `deploy.sh` | interactive questions and the first deployment |
| `converge.sh`, `upgrade.sh`, `verify.sh`, `backup.sh`, `check.sh`, `destroy.sh` | operations on the installation |
| `install.sh`, `scripts/` | installation from a release and the `matrix-deploy` CLI |
| `ansible/` | playbooks and roles |
| `docs/` | documentation (`docs/ru/` — Russian version) |
| `tools/`, `tests/` | helper tools and tests |

**On the server**

| Path | What's there |
| --- | --- |
| `/opt/matrix-deploy/source` | the installer (this repository) |
| `/etc/matrix-deploy/deployment.yml` | your installation answers: domain, email, network mode |
| `/etc/matrix-deploy/versions.yml` | pinned application versions |
| `/opt/matrix/` | service data and configuration |
| `/opt/matrix/.secrets/` | generated passwords and keys (root only) |
| `/var/backups/matrix-deploy/` | backups |

## Documentation

- [`docs/INSTALL.md`](docs/INSTALL.md) — step-by-step server installation;
- [`docs/TECHNICAL.md`](docs/TECHNICAL.md) — how everything works inside;
- [`docs/INSTALLER.md`](docs/INSTALLER.md) — the release installer and publishing releases;
- [`docs/RESTORE.md`](docs/RESTORE.md) — backup format and restore;
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — plans and development status.

Russian documentation lives in [`docs/ru/`](docs/ru/) and [`README.ru.md`](README.ru.md).

## Status

**v1.0.1**: the project is now bilingual. The installer, playbook and verifier
speak English, and the documentation is in English by default with a Russian
version in [`docs/ru/`](docs/ru/). No changes to deployment logic.

**v1.0.0** was the first stable release. Tested on a real Ubuntu 24.04 server:
a clean install with real DNS and Let's Encrypt certificates, federation with
another Matrix server, and audio and video calls between clients on different
networks. What is not yet tested and what is planned is in
[`docs/ROADMAP.md`](docs/ROADMAP.md). The changelog is on the
[Releases](https://github.com/guestikchatgpt/matrix_deploy/releases) page.

## License

[MIT](LICENSE).
