# Matrix Deploy: technical documentation

**English** | [Русский](ru/TECHNICAL.md)

This document describes how the deployment works inside: version selection,
preflight, workflows, the network model, certificates, MatrixRTC, security
and CI. For installation and day-to-day operation, [`README.md`](../README.md)
and [`INSTALL.md`](INSTALL.md) are enough.

Related technical documents:

- [`INSTALLER.md`](INSTALLER.md) — the GitHub Releases installer, the
  `matrix-deploy` CLI, publishing releases;
- [`RESTORE.md`](RESTORE.md) — backup format and restore order;
- [`ROADMAP.md`](ROADMAP.md) — invariants, implementation status and release gates.

## Architecture

Ansible runs with a local controller: the playbook runs on the target server
itself (`inventory/hosts.yml` — `localhost`, `ansible_connection: local`). The
controller is a dedicated `.venv` with pinned ansible-core and collections
(`bootstrap.sh`, `ansible/requirements.yml`).

`site.yml` roles in execution order: `common`, `firewall`, `fail2ban`,
`docker`, `nginx`, `postgres`, `synapse`, `element_web`, `element_call`,
`synad` (Ketesa), `livekit`, `element_jwt`, `coturn`, `verification`.

PostgreSQL, Synapse, Element Web, Ketesa, Element Call, lk-jwt-service and
LiveKit run in Docker containers (LiveKit uses host networking for RTC/TURN).
Nginx + Certbot, Coturn, UFW and Fail2ban run on the host.

The repository deliberately keeps two independent calling paths: classic
Matrix calls go through the system Coturn, while MatrixRTC / Element Call goes
through LiveKit with its own embedded TURN. These TURN stacks must not be
merged.

## Application version selection

Application versions are **not hardcoded in the playbook** and are not taken
from a floating `:latest` when containers start.

`ansible/inventory/group_vars/all/versions.yml` contains only the version
resolution policy: upstream GitHub repositories, Docker image repositories,
release tag -> image tag mapping rules and the allowed PostgreSQL major.

During the first preflight, Matrix Deploy:

1. fetches the latest stable release of each application via GitHub Releases;
2. excludes drafts/prereleases using stable release semantics;
3. for Docker Hub images, additionally checks the corresponding tag via the
   Docker Hub API;
4. for all Docker Hub/GHCR images, verifies via `skopeo` that the selected
   image/tag actually exists for the host architecture;
5. for PostgreSQL, selects the latest published stable patch within the
   allowed major, using Docker Official Images metadata;
6. atomically saves the exact set of selected versions to
   `/etc/matrix-deploy/versions.yml` with mode `0600`;
7. after Docker is installed, pre-pulls exactly these image refs and then
   starts the containers with the same refs.

This way a new installation gets the latest stable versions at deployment
time, while each specific deployment stays reproducible.

A regular `converge.sh` checks upstream again and reports available updates,
but **does not rewrite the version lock or upgrade containers on its own**.
An explicit upgrade is done via `upgrade.sh`: backup first, then a lock file
refresh, then converge with the new exact set.

Each component in the policy has a `min_version` — the minimum stable release
the current configuration has been tested with (this is not a pin). The
resolver never selects a lower version, and a regular `converge.sh` refuses to
apply the configuration to a lock with older versions and asks you to run
`upgrade.sh`. Current minimums (2026-09-25): Synapse v1.161.0, Element Web
v1.12.29, Element Call v0.26.0, LiveKit v1.13.7, lk-jwt-service 0.7.0, Ketesa
v1.5.0, PostgreSQL 18.6.

PostgreSQL is a special case: its major version is set by the
`postgresql_major` policy and is never raised automatically. The resolver
only selects the latest stable release within the allowed major. A PostgreSQL
major change is a separate migration.

If GitHub/Docker registries are temporarily unreachable during a regular
converge, the existing lock is kept. On the first deploy or an explicit
`upgrade.sh`, failing to resolve/verify versions is an error: a deployment
must not start with an unverified set of images.

## Image sources: digest, fallback and mirrors

Registries can be unreachable: `ghcr.io` and the GitHub API may time out
(filtering, throttling), and Docker Hub may fail or rate-limit anonymous
requests. That is why every image is addressed by its **manifest digest**, and
there is a chain of sources to fetch it from.

**Preflight / resolver** (`tools/resolve_versions.py`, `tools/registry.py`):

- the version comes from GitHub Releases; if the GitHub API is unreachable,
  from the tag list in the registry (upstream → official alternative →
  mirrors) with the same stable regex and `min_version`. PostgreSQL: Docker
  Official Images metadata → Docker Hub API → registry tags;
- for the selected tag, the digest is computed (`sha256` of the raw manifest
  index) and the presence of the host platform is checked. Trusted sources
  are the upstream registry and official alternatives (`alternate_images` in
  `versions.yml`, e.g. Ketesa on Docker Hub with the same digest). A single
  public mirror is not trusted on its own: without a reachable upstream, a
  digest is accepted only if **two independent mirrors** confirm it;
- `digest` and `digest_source` are written to the lock. For older locks
  without digests, a regular converge adds digests for the **already pinned**
  versions without changing them (`LOCK_DIGESTS_ADDED`; preflight in
  `check.sh` does the same);
- the Docker Hub API cross-check is fatal only on a definitive answer ("tag
  does not exist"); a network failure is a warning;
- HTTP requests and skopeo calls are retried with backoff.

**Pulling** (`tools/pull_images.py`, `docker` role): for each component,
`docker pull <source>@<digest>` is tried in turn from upstream, the official
alternative registry and the mirrors, with a timeout
(`matrix_image_pull_timeout`) and retries; then `docker tag` to the canonical
reference the containers start from, and a check that the image has the
expected digest. Docker verifies the content against the digest, so a mirror
cannot substitute the image. An image already present locally with the right
digest is not pulled again. If the lock has no digest, mirrors are not used.

**Configuration** (`group_vars/all/main.yml`, overridable in
`/etc/matrix-deploy/deployment.yml`):

```yaml
matrix_registry_mirrors:        # mirrors per upstream registry, host[:port] only
  docker.io: [mirror.gcr.io, dockerhub.timeweb.cloud]
  ghcr.io: [ghcr.nju.edu.cn, ghcr.m.daocloud.io]
matrix_registry_proxy: ""       # e.g. http://user:pass@proxy:3128
matrix_image_pull_timeout: 900
```

`matrix_registry_proxy` is applied to dockerd (systemd drop-in
`docker.service.d/http-proxy.conf`) and to preflight. Open public HTTP proxies
are not used by default: they are unreliable and see all traffic; your own
proxy has to be set explicitly. CI checks whether the public mirrors serve the
real images in an informational step, "Probe public registry mirrors".

## Bootstrap and launcher

`bootstrap.sh`:

1. requires Ubuntu 24.04 LTS (`noble`) and running as root;
2. installs controller dependencies, including `skopeo` for registry checks;
3. creates `.venv`;
4. installs the pinned versions of `ansible-core` and the collections;
5. starts the interactive launcher `deploy.sh`.

The launcher asks for:

- the base domain and the service hostname prefixes;
- the Let's Encrypt email;
- enabling or disabling federation;
- confirmation of the public IPv4;
- the TURN topology: NAT or direct public IP;
- the initial Matrix administrator password.

It detects the port of the active SSH session, validates the operator's input,
saves the non-secret topology to `/etc/matrix-deploy/deployment.yml`, runs
preflight, resolves and pins the current stable versions in
`/etc/matrix-deploy/versions.yml`, prints the deployment plan and requires
confirmation before running the main playbook.

To only prepare the Ansible environment without starting the interactive
deployment:

```bash
sudo ./bootstrap.sh --prepare-only
```

## Preflight contract

A new deployment is rejected if the basic prerequisites are not met.
Preflight checks:

- Ubuntu 24.04 LTS;
- effective root privileges;
- minimum CPU/RAM/free disk space;
- validity of the public and relay IPv4;
- exact match of the local IP to the selected direct-public or NAT mode;
- DNS A records for all Matrix service names, with no extra A addresses;
- unexpected AAAA records when IPv6 is disabled;
- port conflicts on a clean host;
- reachability of apt repositories;
- the latest stable application releases;
- existence of the corresponding Docker image tags;
- availability of the selected image for the current host architecture;
- integrity of the runtime version lock.

The DNS A/AAAA check performs direct DNS queries rather than relying on
NSS-style name resolution. So IPv4-mapped IPv6 addresses cannot be mistaken
for published AAAA records.

IPv6 is currently **not implemented** as a production deployment mode.
Publishing AAAA records with `matrix_ipv6_enabled=false` is treated as a
configuration error rather than ignored while pretending to support IPv6.

## Standard operations: how they work

### Re-applying the configuration of an existing installation

```bash
sudo ./converge.sh
```

It uses:

```text
/etc/matrix-deploy/deployment.yml
/etc/matrix-deploy/versions.yml
```

Preflight checks whether new stable upstream releases have appeared, but a
regular converge does not change the version lock. So re-applying the
configuration is never a hidden application upgrade.

If the administrator account already exists, the Matrix administrator
password is not asked for again.

Running `bootstrap.sh`/`deploy.sh` again on a host that already has
`/etc/matrix-deploy/deployment.yml` is refused: it would overwrite the
topology and upgrade versions without a backup.

If the first deployment was interrupted after the topology was saved but
before the initial `@admin` account was created, you can resume without
re-entering the topology:

```bash
sudo ./converge.sh --admin-password
```

The recovery password is entered without echo, kept only in a temporary file
under `/run/matrix-deploy/` for the duration of the Ansible run, and removed
by a shell trap. It is not stored in `/etc/matrix-deploy`.

The `--refresh-versions` flag exists for an explicit lock file refresh and is
normally invoked via `upgrade.sh`, not manually.

### Ansible check/diff

```bash
sudo ./check.sh
```

`check.sh` deliberately works only with an already deployed managed
installation. Before running Ansible, it requires an existing
`/etc/matrix-deploy/versions.yml`, all persistent generated secrets and both
complete certificate/key pairs. If the managed state is incomplete, the
script requires a real converge/recovery.

It then runs preflight, followed by `site.yml --check --diff`. Preflight may
check for newer upstream releases, but `check.sh` never creates or updates the
version lock.

Runtime reconciliation and health checks that cannot be meaningfully
simulated are skipped in check mode. The certificate SAN state is still read,
and the playbook reports whether each lineage would need reconciliation.

Check mode is a desired-state inspection tool, not a replacement for a real
converge and the final verifier.

### State verification

```bash
sudo ./verify.sh
```

The installed verifier checks system services, containers, listeners, UFW,
PostgreSQL, Synapse client/federation endpoints, local/closed metrics
behavior, web applications, MatrixRTC discovery, both TURN stacks, TLS
certificates, certificate copy consistency, Certbot/ACME routing, Fail2ban and
Nginx syntax.

The regular verifier is deliberately **NAT-safe**. Local service self-checks
connect to the listeners via `127.0.0.1` while keeping the correct HTTP Host
and TLS SNI. So a NAT deployment does not need hairpin NAT just to check its
own Nginx/TURN configuration.

This also means the regular verifier does not claim to confirm external
reachability from the Internet. External federation, TURN relay and MatrixRTC
behavior remain release/integration tests.

For a heavier certificate renewal simulation:

```bash
sudo ./verify.sh --deep
```

This additionally runs:

```text
certbot renew --dry-run --run-deploy-hooks
```

The Certbot staging check verifies external ACME reachability and at the same
time tests the deploy hooks bound to specific certificate lineages.

### Backups

Regular backup of configuration and database:

```bash
sudo ./backup.sh
```

Full backup including the Synapse media store:

```bash
sudo ./backup.sh --include-media
```

Backups are stored in `/var/backups/matrix-deploy/<timestamp>/` with mode
`0700`. They include a logical PostgreSQL dump in custom format, managed
secrets and configuration, `/etc/matrix-deploy` together with the
deployment/version lock, Synapse signing key/appservice state, certificate
state, Docker/UFW inventories and a manifest. Backup files are forced to
root-only mode `0600`; this matters especially for `docker-inspect.json`,
which may contain secrets from container environments.

The raw PostgreSQL data directory is deliberately not archived: PostgreSQL is
backed up logically via `pg_dump`.

A regular backup **does not include** the potentially large media store
unless `--include-media` is given explicitly.

### Explicit upgrade to the latest stable upstream versions

```bash
sudo ./upgrade.sh
```

`upgrade.sh` asks for confirmation and creates a backup first. Then preflight
queries upstream again, verifies the registry and **atomically replaces**
`/etc/matrix-deploy/versions.yml` with the new exact set. The Docker role
pre-pulls the selected image refs, and then a regular converge runs.

This is the only standard workflow that deliberately moves application
versions forward. A regular `converge.sh` does not change the lock.

The PostgreSQL major is not changed automatically in the process. Only the
stable patch within `postgresql_major` is updated; the major version/data
directory guard prevents silently starting an incompatible database.

### Removing the deployment

```bash
sudo ./destroy.sh
```

Destroy is deliberately protected by extra confirmations. You must type the
exact strings:

```text
BACKUP <matrix-server-name>
DESTROY <matrix-server-name>
```

A **full backup including the media store** is created before the second
confirmation. Destroy removes the Matrix containers and data, the Matrix state
in Nginx/Coturn, both Matrix certificate lineages and the Matrix-specific
firewall rules. The following are kept:

- `/var/backups/matrix-deploy`;
- installed system packages;
- SSH firewall access;
- the general HTTP/HTTPS firewall rules.

Automated restore is deliberately not provided yet. See
[`RESTORE.md`](RESTORE.md).

## Public network model

Default public ports:

| Purpose | Protocol/port |
| --- | --- |
| HTTP / ACME | TCP 80 |
| HTTPS / Matrix web entry | TCP 443 |
| Federation, if enabled | TCP 8448 |
| Classic Coturn | TCP+UDP 3478 |
| Classic Coturn TLS/DTLS | TCP+UDP 5349 |
| Classic Coturn relay | UDP 57000-57999 |
| LiveKit RTC TCP fallback | TCP 7881 |
| LiveKit RTC media | UDP 62000-62999 |
| LiveKit embedded TURN | UDP 3480 |
| LiveKit embedded TURN/TLS | TCP 5449 |
| LiveKit embedded TURN relay | UDP 63000-63999 |

The following are local/backend services and must never be deliberately
exposed through UFW:

- LiveKit HTTP/API `7880`;
- Synapse `8008`;
- PostgreSQL `5432`;
- Element/Ketesa/Call/JWT loopback ports `8081-8084`.

TURN/TLS on public TCP 443 is not part of this architecture, since Nginx
already occupies 443 on the same public IP. Such a fallback needs a separate
IP or a purpose-built L4/SNI frontend.

## Certificates

Two Let's Encrypt lineages are used:

1. a SAN certificate named after the Synapse hostname and covering Synapse,
   Element, Ketesa, Element Call and LiveKit;
2. a separate classic TURN certificate.

Every HTTP vhost serves `/.well-known/acme-challenge/` directly from the
shared webroot before redirecting regular HTTP traffic. The role compares the
actual SAN set, can recover an incomplete lineage and does not use `creates:`
as the source of truth for certificate state.

The Certbot deploy hook is lineage-aware:

- on Matrix SAN renewal, Nginx is reloaded and only LiveKit is
  refreshed/restarted;
- on TURN renewal, only Coturn is refreshed/restarted.

LiveKit uses a stable certificate copy in `/opt/matrix/livekit/certs` rather
than bind-mounting individual symlink targets from Let's Encrypt.

## MatrixRTC: how clients find LiveKit

The current upstream model (checked 2026-09-25 against the documentation of
Element Call, Synapse 1.161.0 and lk-jwt-service 0.7.0):

- **Primary path** — the homeserver. `homeserver.yaml` contains
  `matrix_rtc.transports`, and `experimental_features.msc4143_enabled` enables
  the endpoint `GET /_matrix/client/unstable/org.matrix.msc4143/rtc/transports`
  (MSC4519, requires a token). Since Element Call v0.24.0, discovery via
  `.well-known` is deprecated.
- **Fallback** — `org.matrix.msc4143.rtc_foci` in `/.well-known/matrix/client`
  is kept for clients that still read it (Element clients kept it as a
  fallback path; Synapse does not generate this key itself).
- `livekit_service_url` in `matrix_rtc.transports` is deprecated in Synapse
  1.161.0, but upstream requires keeping it for compatibility. The new `url`
  property implies lk-jwt-service in application service mode
  (MSC4195/MSC4512), which upstream still calls experimental, so it is not
  enabled.
- lk-jwt-service validates users' OpenID tokens via
  `/_matrix/federation/v1/openid/userinfo`, finding the homeserver via
  `/.well-known/matrix/server`. Therefore, with federation disabled, Synapse
  still enables the `openid` listener resource, and `.well-known/matrix/server`
  still points to `:443`; federation itself stays disabled
  (`federation_domain_whitelist: []`, no `federation` resource, 8448 closed).
- LiveKit sends participant events to lk-jwt-service at
  `http://127.0.0.1:8084/sfu_webhook` (delegated delayed leave, MSC4140); a
  pull-based check `LIVEKIT_SANITY_CHECK_INTERVAL_SECONDS=60` is also enabled.

The verifier checks every link: the transports endpoint (401 without a token
= enabled), OpenID userinfo, the `.well-known` fallback and the webhook.

## Synapse Admin endpoint

The general Synapse hardening rule is not to expose unnecessary `/_synapse/`
endpoints. This deployment exposes only `/_synapse/client/` and
`/_synapse/admin/`, and returns 404 for the rest of the `/_synapse/` space.

Exposing `/_synapse/admin/` is a deliberate architectural decision: Ketesa is a
browser-based admin interface and needs the Synapse Admin API. Access still
requires Matrix administrator authorization. Synapse metrics stay closed and
are only checked locally.

## Fail2ban

The first supported Fail2ban policy is deliberately conservative: an `sshd`
jail with the automatically detected SSH port, the `systemd` backend and a
UFW action.

Matrix/Nginx-specific filters are not enabled until their regexes have been
validated against real deployment logs with `fail2ban-regex`. Avoiding false
bans matters more than adding unverified filters.

## State and secrets

Important paths:

```text
/etc/matrix-deploy/deployment.yml       saved topology and operator choices
/etc/matrix-deploy/versions.yml         exact version lock selected by preflight
/opt/matrix/.secrets/                   generated original secrets (root only)
/opt/matrix/                            managed application state
/var/www/matrix/                        Matrix well-known + ACME webroot
/var/backups/matrix-deploy/             protected backups
/usr/local/sbin/matrix-stack-verify     installed verifier
/opt/matrix-deploy/                     installed installer release and the matrix-deploy CLI
```

Do not commit `/etc/matrix-deploy`, `.venv`, generated secrets, certificates
or backup archives to Git.

The `matrix` system account is deliberately not in the Docker group: access
to the Docker socket is effectively equivalent to root, and the applications
do not need it.

## CI and release criteria

GitHub Actions runs:

- YAML and static invariant checks, including a ban on reintroducing
  hardcoded top-level `*_image` pins in the version policy, floating `:latest`
  and known deprecated Ansible patterns;
- a real `bootstrap.sh --prepare-only` run on Ubuntu 24.04;
- a real runtime preflight querying GitHub Releases, Docker Official
  Images/Docker Hub and registry validation via `skopeo`;
- a check that a repeated regular preflight re-checks upstream but does not
  change the SHA of the existing `/etc/matrix-deploy/versions.yml`;
- a manifest check of all **dynamically selected** image refs with mandatory
  `linux/amd64` and `linux/arm64` support;
- shell and Python resolver syntax checks;
- a real NAT-mode preflight on an Ubuntu 24.04 runner with DNS A/AAAA queries
  and resource, port, routing and apt checks;
- inventory parsing;
- `ansible-playbook --syntax-check` for the deployment, preflight,
  verification, backup and destroy playbooks;
- a real Jinja render of the verifier with federation enabled and disabled,
  and `bash -n` for both variants.

The `ansible-core` and collection versions are still pinned in the repository,
because they are the installer's own runtime, not the upgradable Matrix
application stack. The Ansible configuration disables deprecated top-level
fact injection; roles use `ansible_facts[...]`. Third-party apt repositories
are managed via deb822.

These CI gates are necessary but not sufficient. The dynamic resolver
confirms that individual upstream releases exist and are stable, but it does
not replace an integration check of the whole set's compatibility as a single
stack.

Before publishing a release, the remaining release gates from
[`ROADMAP.md`](ROADMAP.md) must pass: a full deployment on a clean Ubuntu with
real DNS/ACME, a second converge/idempotence run, `check.sh`, reboot, Certbot
dry-run, federation, classic TURN and MatrixRTC calls, and a
backup/destroy/restore rehearsal before enabling automated restore.
