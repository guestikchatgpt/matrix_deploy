# Matrix Deploy hardening roadmap

**English** | [Русский](ru/ROADMAP.md)

This repository started from an Ansible baseline imported before the production fixes made to a real Matrix stack on 2026-08-18.

The goal remains the same: a reusable installer for a clean Ubuntu 24.04 LTS server. The production state serves as a reference implementation, not as a source of hardcoded domains, IP addresses, credentials or one-off assumptions about a specific host.

## Invariants

- The primary target OS is Ubuntu 24.04 LTS.
- Re-running the playbook must be safe.
- The system Coturn for classic calls and LiveKit's embedded TURN are separate, deliberately independent TURN stacks; they must not be merged.
- LiveKit's HTTP/API port 7880 must stay internal; Nginx is the public HTTPS entry point.
- Federation is a real feature flag and must consistently control Synapse, Nginx, UFW and verification. Exception: `.well-known/matrix/server` is always published, because lk-jwt-service uses it to find the OpenID endpoint for MatrixRTC.
- Secrets must never end up in Git.
- An existing installation must be backed up before destructive operations and upgrades.
- A regular converge must not silently upgrade application images.
- A new deployment must use the latest stable upstream releases, but each specific deployment must remain reproducible through an exact runtime lock.
- The PostgreSQL major version must never be raised automatically.

## P0 — fixes proven in production

1. Replace the old Synapse Admin image with Ketesa, keeping the existing role name for the duration of the functional refactoring.
2. Move lk-jwt-service to a secure configuration model:
   - use `LIVEKIT_JWT_BIND` instead of the deprecated port configuration;
   - set `LIVEKIT_FULL_ACCESS_HOMESERVERS` explicitly;
   - add the LiveKit -> lk-jwt-service `/sfu_webhook` webhook only from the release that ships it (lk-jwt-service >= 0.7.0; added 2026-09-25).
3. Keep `LiveKit room.auto_create=false`.
4. Set LiveKit's embedded TURN relay range explicitly: 63000-63999/udp.
5. Keep the LiveKit RTC media range 62000-62999/udp and the TCP fallback 7881/tcp.
6. Do not expose LiveKit 7880/tcp through UFW.
7. Copy Let's Encrypt certificates into a stable LiveKit certificate directory and mount that directory into the container.
8. Make the Certbot deploy hook depend on the certificate lineage:
   - Matrix SAN certificate: reload Nginx, refresh/restart LiveKit only;
   - legacy TURN certificate: refresh/restart Coturn only.
9. In every HTTP vhost, serve `/.well-known/acme-challenge/` directly from the shared webroot before redirecting the rest of the traffic to HTTPS.
10. Compare the certificate's actual SAN set instead of relying on `creates:` alone.
11. Restrict public routing of `/_synapse/` to the required client/admin endpoints; keep metrics closed.
12. Enable Synapse metrics explicitly when they are configured.
13. Extend the deny ranges for Synapse URL preview SSRF protection.
14. Add targeted Coturn `denied-peer` ranges without blindly blocking all RFC1918 networks.
15. Create a separate Fail2ban role, starting with basic SSH protection; enable Matrix/Nginx filters only after validating the regexes against real logs.
16. Replace the original verification script with the production-proven verifier model: no ANSI escape sequences in non-TTY output, no false `pipefail`/`grep -q` errors, and explicit checks of both TURN stacks.

## PostgreSQL

- Replace the unused/aggressive tuning model with a deterministic, Ansible-managed configuration.
- Use conservative, resource-aware defaults; do not set a huge per-operation `work_mem`.
- Fix the PostgreSQL readiness logic: check the module result, not just the task's overall success.
- Add a major-version/data-directory guard before updating the container.
- Keep the PostgreSQL 18 behavior with `PGDATA=/var/lib/postgresql/data/pgdata`.
- The version resolver must only move automatically through stable patch releases within `postgresql_major`; a major change remains a separate migration.

## Versions and upgrades

The target model no longer requires manually bumping release tags in Git every few weeks.

- `ansible/inventory/group_vars/all/versions.yml` holds the **source policy**, not the current application versions.
- For applications, the latest stable release is determined via GitHub Releases.
- Docker Hub images are additionally cross-checked via the Docker Hub API.
- All Docker Hub/GHCR refs are verified via registry inspection for the host architecture.
- For PostgreSQL, Docker Official Images metadata is used and the latest stable patch within the allowed major is selected.
- The preflight result is saved as the exact runtime lock `/etc/matrix-deploy/versions.yml`.
- The Docker role pre-pulls exactly the refs from the lock before starting the application stack.
- A regular `converge.sh` checks upstream but does not change the lock.
- `upgrade.sh` first creates a backup, then explicitly refreshes the lock and applies the new set.
- If upstream is unreachable, a regular converge may keep working with the existing lock; a fresh deploy/explicit upgrade must fail if the new set cannot be reliably resolved and verified.
- `site.yml` must not perform deployment mutations without a valid version lock.
- Floating `:latest` in the runtime lock and hardcoded top-level `*_image` pins in the repository policy are forbidden by static checks.
- CI must actually run the resolver and verify exact refs and multiarch manifests.
- CI must prove that a repeated regular preflight does not rewrite an existing lock.
- The `ansible-core` and collection versions stay pinned as the installer's own runtime and are updated separately after compatibility validation.
- Do not use deprecated top-level injection of Ansible facts; roles must use `ansible_facts[...]`, and injection must be disabled.
- Third-party apt repositories must be managed via deb822 sources rather than the deprecated `apt_repository` task.

## Nginx and ACME

- Guarantee an actual reload of the bootstrap Nginx configuration before the first Certbot run.
- Check HTTP-01 routing before requesting certificates from Let's Encrypt.
- Keep a shared SAN certificate for Synapse, Element, Ketesa, Element Call and LiveKit.
- Keep the TURN certificate as a separate lineage.
- Add a deep verification workflow for `certbot renew --dry-run --run-deploy-hooks` instead of running it on every converge.

## LiveKit / MatrixRTC

- Transport discovery (since 2026-09): the primary path is the homeserver endpoint `GET /_matrix/client/unstable/org.matrix.msc4143/rtc/transports` (MSC4519) from `matrix_rtc.transports` in `homeserver.yaml`; the `.well-known` `org.matrix.msc4143.rtc_foci` is only a deprecated client fallback (Element Call v0.24.0).
- `matrix_rtc.transports[].livekit_service_url` is deprecated since Synapse 1.161.0 but required for backward compatibility. The new `url` property should only be enabled together with lk-jwt-service's application service mode (MSC4195/MSC4512) — upstream still marks it experimental.
- lk-jwt-service validates OpenID tokens via `/_matrix/federation/v1/openid/userinfo`, finding the homeserver via `.well-known/matrix/server` (otherwise `:8448`). Therefore, with federation disabled, the `openid` listener and `m.server` in `.well-known/matrix/server` are still required.
- `room.auto_create=false`.
- `LIVEKIT_FULL_ACCESS_HOMESERVERS` must contain only the local Matrix server name and the required local domain aliases.
- Embedded TURN: 3480/udp, 5449/tcp, relay 63000-63999/udp.
- RTC: 7881/tcp and 62000-62999/udp.
- The public entry point via Nginx stays on 443; TURN/TLS on 443 is a separate architectural task that requires another IP or L4/SNI routing.

## Classic Coturn

- Keep ports 3478/tcp+udp, 5349/tcp+udp and relay 57000-57999/udp.
- Distinguish installations with a direct public IP from installations behind NAT. Never assume universally that `relay-ip == external-ip`.
- Verify the relay separately from LiveKit's embedded TURN.

## Synapse

- Make `synapse_enable_federation` a real end-to-end switch or remove it; preferably wire it through all related roles.
- Make operational policy explicit variables (`report_stats`, user-directory policy, metrics).
- Keep the current MatrixRTC delayed events and rate limit settings until upstream requirements change.
- Replace the administrator's filesystem marker as the source of truth with a real check of Synapse/user state.

## Firewall and IPv6

- Build firewall rules from a single variable model instead of duplicated hardcoded lists.
- Detect the active SSH port before enabling UFW's deny-incoming policy.
- Apply the baseline firewall policy early enough that services are never publicly reachable during deployment.
- Treat IPv6 as an explicitly supported or unsupported mode. An AAAA record with IPv6 disabled must be caught by preflight, not silently accepted.

## Bootstrap / operator UX

Add an operator-facing bootstrap/launcher around Ansible that, before deployment, performs:

- Ubuntu/version and privilege checks;
- RAM/CPU/disk checks;
- internet/apt/dependency checks;
- external IPv4 detection with operator confirmation;
- domain/FQDN generation and confirmation;
- A/AAAA checks;
- local port conflict checks;
- NAT/direct-public detection;
- SSH port detection;
- detection of an existing installation/config/certificates;
- resolution and registry validation of the latest stable application versions;
- saving the exact version lock;
- printing an explicit deployment plan and asking for confirmation.

Bootstrap remains orchestration UX; the Matrix service logic must live in Ansible roles.

## Lifecycle workflows

Provide explicit workflows for backup, upgrade, rollback metadata and destroy. Destroy must require strict confirmation and must not silently delete the latest backup. The runtime version lock is part of the operational state and must be included in backup/restore.

## Acceptance criteria

Before a release candidate is considered complete, it must pass:

1. syntax/static checks;
2. runtime resolution of stable versions and registry verification;
3. deployment on a clean Ubuntu 24.04;
4. a second Ansible run without unintended changes and without changing the version lock;
5. a desired-state check of the installed host via `check.sh`;
6. a state persistence test after reboot;
7. a Certbot dry-run with deploy hooks;
8. inbound and outbound federation checks;
9. a classic TURN relay check;
10. local and federated MatrixRTC audio/video calls, ideally with verification of the embedded TURN relay;
11. a backup/destroy/restore rehearsal on a disposable host;
12. a final verifier run with no required FAILs.

## Implementation status (v1.0.0)

### Implemented in code

- [x] Universal topology/subdomain model; no hardcoded production domain/IP in executable code.
- [x] Dynamic resolver for the latest stable application releases via GitHub Releases + Docker Official Images/Docker Hub + registry validation.
- [x] Exact runtime version lock `/etc/matrix-deploy/versions.yml`; application release tags no longer require manual changes in the repository.
- [x] PostgreSQL is upgraded automatically only within the allowed major.
- [x] The Docker role pre-pulls exact image refs from the lock before starting the application stack.
- [x] A regular converge does not rewrite the lock; an explicit upgrade does backup -> refresh lock -> converge.
- [x] CI actually runs the dynamic resolver and verifies every selected image in the registry, including linux/amd64 and linux/arm64 manifests.
- [x] Static checks forbid reintroducing hardcoded top-level application image pins and `:latest`.
- [x] Controller/collection versions are pinned for reproducibility of the installer itself.
- [x] Interactive bootstrap/launcher with topology persistence, input validation and preflight.
- [x] CI actually runs `bootstrap.sh --prepare-only` on an Ubuntu 24.04 runner.
- [x] CI actually runs NAT-mode preflight with real DNS A/AAAA resolution and resource, port, routing and apt checks.
- [x] Direct-public and NAT are distinguished, the local IP is matched exactly, and the port of the active SSH session is detected.
- [x] DNS validation uses direct A/AAAA queries, so IPv4-mapped NSS results do not cause false AAAA failures.
- [x] Explicit behavior with IPv6 disabled, rejecting unexpected AAAA records.
- [x] Ansible roles use `ansible_facts[...]`; deprecated top-level fact injection is disabled.
- [x] Docker and Nginx repositories use `deb822_repository`; the deprecated `apt_repository` is forbidden by a static check.
- [x] Deterministic PostgreSQL configuration, conservative tuning and a major-version guard.
- [x] Synapse policy/metrics/federation and database-backed administrator reconciliation are configured.
- [x] Recovery path for a partial deployment with temporary secret handoff via `converge.sh --admin-password`.
- [x] Element permalink fixed and migration to Ketesa done.
- [x] Production-proven LiveKit/JWT MatrixRTC security model and an explicit embedded TURN relay range.
- [x] Independent hardened classic Coturn setup with NAT mapping support.
- [x] ACME-safe bootstrap/production Nginx routing, SAN reconciliation, recovery of incomplete lineages and a selective deploy hook.
- [x] Desired-state UFW, including removal of the stale public 7880 rule.
- [x] Separate Fail2ban role with a basic SSH jail and handling of the control socket race.
- [x] Production-proven parameterized verifier and an optional deep Certbot renewal test.
- [x] NAT-safe self-verification over loopback with correct Host/SNI, without requiring hairpin NAT.
- [x] Docker container logs are limited; the Matrix service account does not get privileges via the Docker group.
- [x] `converge.sh`, check-mode-aware `check.sh`, backup-first `upgrade.sh` and a protected `destroy.sh`.
- [x] Protected backup format with root-only artifacts; destructive destroy requires a full backup including Synapse media.
- [x] GitHub Actions gate for static/YAML/shell/Python/Ansible syntax.
- [x] CI renders and shell-validates verifier variants with federation enabled and disabled.
- [x] Operator README and an explicit restore runbook/contract.
- [x] One-command installation from a stable GitHub Release (`install.sh`, SHA-256 verification, `bootstrap.sh --install`) and the `matrix-deploy` CLI on top of converge/upgrade/verify/check/backup/destroy; `matrix-deploy update` with rollback if preparing the new release fails.
- [x] Re-running `deploy.sh` on top of an existing installation is refused (no hidden topology overwrite or lock refresh without a backup).
- [x] With federation disabled, Synapse also blocks outbound federation (`federation_domain_whitelist: []`); the verifier does not require metrics when `synapse_enable_metrics=false`.
- [x] MatrixRTC brought in line with the current upstream model (2026-09-25): discovery via the homeserver's `rtc/transports`, `.well-known` as a fallback; OpenID for lk-jwt-service works with federation disabled too; LiveKit webhook -> `/sfu_webhook` for delegated leave; Element Web/Element Call/Ketesa configs follow current schemas; the verifier checks transports, OpenID and the webhook.
- [x] `min_version` in the version policy: the resolver never selects versions below the tested ones, and converge refuses to apply the config to an older lock (`upgrade.sh` is required).

### Deliberately deferred until integration testing

- [ ] Automated `restore.sh`. The backup format and restore sequence are documented, but automation is not enabled until a full destroy/restore test passes.
- [ ] Fail2ban Matrix/Nginx filters. Add only after validating `fail2ban-regex` against real logs.
- [ ] A full IPv6 deployment mode.
- [ ] TURN/TLS on public TCP 443; requires a separate IP or a purpose-built L4/SNI frontend.

### Release gates that still need a disposable/real Ubuntu host

- [x] Full clean Ubuntu 24.04 installation via `bootstrap.sh` with the project's real DNS and Let's Encrypt, using a dynamically selected exact version lock.
- [ ] A second `converge.sh` run without unintended changes and without changing the lock.
- [ ] `check.sh` on an installed host; confirm the diff is useful and there are no false runtime failures.
- [ ] Reboot and persistence check.
- [ ] `verify.sh --deep` / Certbot staging renewal on a clean-host deployment.
- [x] Inbound and outbound federation checks when enabled (invites and rooms with another server).
- [ ] Authenticated relay test of classic Coturn.
- [x] MatrixRTC audio/video calls between clients on different networks. A dedicated check of the embedded TURN relay has not been done yet.
- [ ] A full backup -> destroy -> restore rehearsal before enabling automated restore; restore exactly the saved version lock rather than silently selecting new versions during disaster recovery.
- [ ] A final verifier run with no required FAILs on a release-candidate host.
