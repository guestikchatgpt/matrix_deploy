# Matrix Deploy

Ansible-based Matrix Synapse deployment for a clean Ubuntu 24.04 LTS host.

The repository keeps two deliberate call paths:

- **legacy Matrix calls** use the system Coturn service;
- **MatrixRTC / Element Call** uses LiveKit with its own embedded TURN server.

These TURN stacks are independent by design and must not be consolidated.

> Status: hardening branch / release candidate preparation. Static, syntax and
> bootstrap/preflight runtime CI are in place. A full clean-host integration test
> with real project DNS and ACME is still required before this branch should be
> merged into `main` or treated as production release.

## What is deployed

- PostgreSQL 18 for Synapse;
- Matrix Synapse;
- Element Web;
- Ketesa (Synapse administration UI);
- Element Call;
- LiveKit;
- Element `lk-jwt-service` for MatrixRTC authorization;
- system Coturn for legacy calls;
- host Nginx and Certbot;
- UFW;
- Fail2ban SSH baseline.

Container/application versions are pinned in
`ansible/inventory/group_vars/all/versions.yml`. Moving `latest` tags are not
accepted in the release-candidate matrix.

## First deployment

The intended workflow is local-controller Ansible: clone the repository onto the
target server and run it there.

```bash
git clone https://github.com/guestikchatgpt/matrix_deploy.git
cd matrix_deploy
git switch agent/roadmap-hardening   # while this PR remains unmerged
sudo ./bootstrap.sh
```

`bootstrap.sh`:

1. requires Ubuntu 24.04 LTS (`noble`) and root;
2. installs the small controller dependency set;
3. creates `.venv`;
4. installs pinned `ansible-core` and collections;
5. starts the interactive `deploy.sh` launcher.

The launcher asks for:

- base domain and service hostname prefixes;
- Let's Encrypt email;
- federation on/off;
- public IPv4 confirmation;
- NAT/direct-public TURN topology;
- initial Matrix administrator password.

It detects the active SSH session port, validates operator input, persists
non-secret topology in `/etc/matrix-deploy/deployment.yml`, runs preflight, prints
the deployment plan, and requires confirmation before the main playbook runs.

To prepare only the Ansible environment without starting the interactive deploy:

```bash
sudo ./bootstrap.sh --prepare-only
```

## Preflight contract

A fresh deployment is rejected when core assumptions are not met. Preflight
checks include:

- Ubuntu 24.04 LTS;
- effective root privileges;
- minimum CPU/RAM/free disk;
- valid public and relay IPv4 values;
- exact local-IP consistency for direct-public versus NAT mode;
- DNS A records for all Matrix service hostnames, with no stray additional A
  addresses;
- unexpected AAAA records while IPv6 mode is disabled;
- fresh-host port conflicts;
- apt repository reachability.

DNS A/AAAA validation queries DNS directly rather than using NSS-family lookup
output, so IPv4-mapped IPv6 addresses cannot be mistaken for published AAAA
records.

IPv6 is currently **not** implemented as a production deployment mode. Publishing
AAAA records while `matrix_ipv6_enabled=false` is treated as a configuration
error rather than pretending IPv6 is supported.

## Routine operations

### Converge an existing installation

```bash
sudo ./converge.sh
```

Uses `/etc/matrix-deploy/deployment.yml`; it does not prompt for the Matrix admin
password when the existing admin account is already present.

If the first deployment was interrupted after topology was saved but before the
initial `@admin` account was created, resume without re-entering the topology:

```bash
sudo ./converge.sh --admin-password
```

The recovery password is read without echo, stored only in a temporary
`/run/matrix-deploy/` file for the Ansible invocation and removed by a shell
trap. It is not persisted in `/etc/matrix-deploy`.

### Ansible check/diff

```bash
sudo ./check.sh
```

`check.sh` is deliberately limited to an already deployed managed installation.
Before Ansible starts it requires all persistent generated secrets and both
certificate/key lineages to exist. If managed state is incomplete, it exits and
asks for a real converge/recovery instead of allowing check mode to generate a
missing secret or certificate as a side effect.

It then runs preflight followed by `site.yml --check --diff`. Runtime
reconciliation and health probes that cannot be meaningfully simulated are
skipped in check mode; certificate SAN state is still read and the playbook
reports whether each lineage would require reconciliation.

Check mode is a desired-state review tool, not a substitute for a real converge
and the final verifier.

### Verify

```bash
sudo ./verify.sh
```

The installed verifier checks system services, containers, listeners, UFW,
PostgreSQL, Synapse client/federation endpoints, local/private metrics behavior,
web applications, MatrixRTC discovery, both TURN stacks, TLS certificates,
certificate-copy consistency, Certbot/ACME routing, Fail2ban and Nginx syntax.

The normal verifier is deliberately **NAT-safe**. Service self-checks connect to
local listeners through `127.0.0.1` while preserving the correct HTTP Host/TLS
SNI. A NAT deployment therefore does not require hairpin NAT merely to verify
its own Nginx/TURN configuration.

This also means the normal verifier does not claim to prove Internet-side
reachability. External federation, TURN relay and MatrixRTC behavior remain
release/integration tests.

For the expensive certificate renewal simulation:

```bash
sudo ./verify.sh --deep
```

This additionally runs:

```text
certbot renew --dry-run --run-deploy-hooks
```

The Certbot staging validation is an external ACME reachability check and also
exercises the lineage-selective deploy hooks.

### Backup

Routine configuration/database backup:

```bash
sudo ./backup.sh
```

Full backup including the Synapse media store:

```bash
sudo ./backup.sh --include-media
```

Backups are written under `/var/backups/matrix-deploy/<timestamp>/` with mode
`0700`. They contain a PostgreSQL custom-format dump, managed secrets/config,
Synapse signing key/appservice state, certificate state, Docker/UFW inventories,
and a manifest. Backup artifacts are forced to root-only `0600`; this matters in
particular for `docker-inspect.json`, which can contain container environment
secrets.

The raw PostgreSQL data directory is intentionally not archived; PostgreSQL is
backed up logically with `pg_dump`.

A routine backup does **not** include the potentially large media store unless
`--include-media` is specified.

### Upgrade to versions pinned in the checkout

```bash
sudo ./upgrade.sh
```

`upgrade.sh` requires confirmation, creates a backup first, then performs a normal
converge. Routine `converge.sh` does not use `pull: true` and therefore is not an
implicit "upgrade everything to latest" operation. PostgreSQL additionally has a
major-version data-directory guard, so changing the configured major cannot
silently start an incompatible database image.

### Destroy

```bash
sudo ./destroy.sh
```

Destroy is deliberately guarded. It requires the exact strings:

```text
BACKUP <matrix-server-name>
DESTROY <matrix-server-name>
```

Before the second confirmation it creates a **full backup including media**.
Destroy removes Matrix containers/data, Matrix Nginx/Coturn state, the two Matrix
certificate lineages and Matrix-specific firewall rules. It preserves:

- `/var/backups/matrix-deploy`;
- installed system packages;
- SSH firewall access;
- generic HTTP/HTTPS firewall rules.

Automated restore is intentionally not exposed yet. See `docs/RESTORE.md`.

## Public network model

Default public ports are:

| Purpose | Protocol/port |
| --- | --- |
| HTTP / ACME | TCP 80 |
| HTTPS / Matrix web ingress | TCP 443 |
| Federation, when enabled | TCP 8448 |
| Legacy Coturn | TCP+UDP 3478 |
| Legacy Coturn TLS/DTLS | TCP+UDP 5349 |
| Legacy Coturn relay | UDP 57000-57999 |
| LiveKit RTC TCP fallback | TCP 7881 |
| LiveKit RTC media | UDP 62000-62999 |
| LiveKit embedded TURN | UDP 3480 |
| LiveKit embedded TURN/TLS | TCP 5449 |
| LiveKit embedded TURN relay | UDP 63000-63999 |

The following are local/backend services and must not be deliberately exposed by
UFW:

- LiveKit HTTP/API `7880`;
- Synapse `8008`;
- PostgreSQL `5432`;
- Element/Ketesa/Call/JWT loopback ports `8081-8084`.

TURN/TLS on public TCP 443 is not part of this architecture because Nginx already
owns 443 on the same public IP. Supporting that fallback requires a separate IP
or an explicit L4/SNI frontend design.

## Certificates

There are two Let's Encrypt lineages:

1. a SAN certificate named after the Synapse hostname, covering Synapse, Element,
   Ketesa, Element Call and LiveKit;
2. a separate legacy TURN certificate.

Every HTTP vhost serves `/.well-known/acme-challenge/` directly from the shared
webroot before redirecting normal HTTP traffic. The role reconciles the actual
SAN set, can recover an incomplete lineage, and does not rely on `creates:` as
certificate state.

The Certbot deploy hook is lineage-aware:

- Matrix SAN renewal reloads Nginx and refreshes/restarts LiveKit only;
- TURN renewal refreshes/restarts Coturn only.

LiveKit consumes a stable certificate copy under `/opt/matrix/livekit/certs`
rather than bind-mounting individual Let's Encrypt symlink targets.

## Synapse Admin endpoint

The general Synapse hardening rule is to avoid publishing unnecessary
`/_synapse/` endpoints. This deployment exposes only `/_synapse/client/` and
`/_synapse/admin/`, and returns 404 for the remaining `/_synapse/` namespace.

`/_synapse/admin/` is an intentional architecture choice here: Ketesa is a
browser-side administration UI and needs the Synapse Admin API. Access still
requires Matrix administrator authentication. Synapse metrics remain private and
are checked locally only.

## Fail2ban

The first supported Fail2ban policy is intentionally conservative: an `sshd` jail
using the detected SSH port, `systemd` backend and UFW action.

Matrix/Nginx-specific filters are not enabled until their regexes are tested
against real deployment logs with `fail2ban-regex`; avoiding false-positive bans
is more important than shipping speculative filters.

## State and secrets

Important paths:

```text
/etc/matrix-deploy/deployment.yml       persisted topology/operator choices
/opt/matrix/.secrets/                   generated source secrets (root-only)
/opt/matrix/                            managed application state
/var/www/matrix/                        Matrix well-known + ACME webroot
/var/backups/matrix-deploy/             protected backups
/usr/local/sbin/matrix-stack-verify     installed verifier
```

Do not commit `/etc/matrix-deploy`, `.venv`, generated secrets, certificates or
backup archives.

The system `matrix` service account is deliberately not a member of the Docker
group; Docker socket access is root-equivalent and is not required by the
applications.

## CI and release gates

GitHub Actions performs:

- YAML/static invariant checks, including rejection of moving `:latest` image
  references and known deprecated Ansible patterns;
- registry manifest validation for every pinned application image, requiring
  both `linux/amd64` and `linux/arm64` support;
- shell syntax checks;
- an actual `bootstrap.sh --prepare-only` run on Ubuntu 24.04 using the pinned
  controller and collection versions;
- an actual NAT-mode `preflight.yml` run on a fresh Ubuntu 24.04 runner, using
  real DNS A/AAAA queries and exercising resource, port, routing and apt checks;
- inventory parsing;
- `ansible-playbook --syntax-check` for deploy, preflight, verification, backup
  and destroy playbooks;
- actual Jinja rendering of the verifier with federation both enabled and
  disabled, followed by `bash -n` on both rendered scripts.

The Ansible configuration disables deprecated top-level fact injection; roles use
`ansible_facts[...]`. Apt repository management uses the deb822 format.

These CI gates are necessary but not sufficient. Before a release candidate is
merged, run the remaining integration gates from `ROADMAP.md`: full clean Ubuntu
deployment with real DNS/ACME, second converge/idempotence, `check.sh`, reboot
persistence, Certbot dry-run, federation, legacy TURN and MatrixRTC calls, plus
backup/destroy/restore rehearsal before restore automation is exposed.
