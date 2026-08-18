# Matrix Deploy hardening roadmap

This repository started as the original Ansible baseline imported before the 2026-08-18 production remediation of the `redacted.ru` Matrix stack.

The target remains a reusable installer for a clean Ubuntu 24.04 LTS host. Production state is a reference implementation, not a source of hard-coded domains, IPs, credentials, or one-off host assumptions.

## Invariants

- Ubuntu 24.04 LTS is the primary target.
- The playbook is expected to be safe to re-run.
- Legacy system Coturn and LiveKit embedded TURN are separate, intentional TURN stacks and must not be merged.
- LiveKit HTTP/API port 7880 is internal-only; Nginx is the public HTTPS ingress.
- Federation is a real feature flag and must consistently control Synapse, Nginx, UFW, well-known data, and verification.
- Secrets must not be committed.
- Existing installations must be backed up before destructive or upgrade operations.
- Routine convergence must not silently upgrade all container images.

## P0 - production-proven fixes

1. Replace legacy Synapse Admin image with Ketesa while retaining the existing role name during the functional refactor.
2. Move lk-jwt-service to the secure configuration model:
   - preferred `LIVEKIT_JWT_BIND` instead of deprecated port configuration;
   - explicit `LIVEKIT_FULL_ACCESS_HOMESERVERS`;
   - no unsupported webhook configuration.
3. Keep `LiveKit room.auto_create=false`.
4. Add explicit LiveKit embedded TURN relay range 63000-63999/udp.
5. Keep LiveKit RTC media range 62000-62999/udp and TCP fallback 7881/tcp.
6. Do not expose LiveKit 7880/tcp in UFW.
7. Copy Let's Encrypt certificates into a stable LiveKit certificate directory and mount the directory into the container.
8. Make Certbot deploy hook lineage-aware:
   - Matrix SAN certificate: reload Nginx, refresh/restart LiveKit only;
   - legacy TURN certificate: refresh/restart Coturn only.
9. Serve `/.well-known/acme-challenge/` directly from the shared webroot in every HTTP vhost before redirecting the rest to HTTPS.
10. Reconcile certificate SAN sets instead of relying only on `creates:`.
11. Restrict public `/_synapse/` routing to required client/admin endpoints; keep metrics private.
12. Enable Synapse metrics explicitly when configured.
13. Expand Synapse URL-preview SSRF deny ranges.
14. Add targeted Coturn denied-peer ranges without blindly blocking all RFC1918 networks.
15. Add a dedicated Fail2ban role with an SSH baseline first; Matrix/Nginx filters require real-log regex validation before enabling.
16. Replace the original verification script with the production-proven verifier model: no ANSI escapes in non-TTY output, no `pipefail`/`grep -q` false failures, and explicit verification of both TURN stacks.

## PostgreSQL

- Replace the unused/aggressive tuning model with deterministic Ansible-managed configuration.
- Keep conservative resource-aware defaults; avoid huge per-operation `work_mem` values.
- Fix PostgreSQL readiness logic to inspect the module result rather than generic task success.
- Add a major-version/data-directory guard before container upgrades.
- Preserve PostgreSQL 18 `PGDATA=/var/lib/postgresql/data/pgdata` behavior.

## Versions and upgrades

- Introduce a tested version matrix for containers.
- Routine `site.yml` runs must converge configuration without silently upgrading images.
- Keep upgrades as an explicit workflow with backup, compatibility checks, deployment, verification, and rollback information.
- Pin Ansible collection versions after compatibility validation.
- Pin every application image to a published release tag; moving `latest` tags are not accepted in the release-candidate matrix.
- Validate every pinned image in CI against the registry and require both linux/amd64 and linux/arm64 manifests.
- Avoid deprecated top-level Ansible fact injection; roles use `ansible_facts[...]` with injection disabled.
- Manage third-party apt repositories with deb822 sources rather than deprecated `apt_repository` tasks.

## Nginx and ACME

- Ensure bootstrap Nginx config is actually reloaded before the first Certbot invocation.
- Validate HTTP-01 routing before asking Let's Encrypt for certificates.
- Preserve the shared SAN certificate for Synapse, Element, Ketesa, Element Call, and LiveKit.
- Keep the TURN certificate as a separate lineage.
- Add a deep verification tag/workflow for `certbot renew --dry-run --run-deploy-hooks` rather than running it during every converge.

## LiveKit / MatrixRTC

- `room.auto_create=false`.
- `LIVEKIT_FULL_ACCESS_HOMESERVERS` must include the local Matrix server name and intended local domain aliases only.
- Embedded TURN: 3480/udp, 5449/tcp, relay 63000-63999/udp.
- RTC: 7881/tcp and 62000-62999/udp.
- Public Nginx ingress remains on 443; TURN/TLS on 443 is a separate architecture project requiring another IP or L4/SNI routing.

## Legacy Coturn

- Keep ports 3478/tcp+udp, 5349/tcp+udp, relay 57000-57999/udp.
- Distinguish direct-public-IP deployments from NAT deployments. Do not assume `relay-ip == external-ip` universally.
- Validate relay behavior separately from LiveKit embedded TURN.

## Synapse

- Make `synapse_enable_federation` a real end-to-end switch or remove it; preferred plan is to wire it through all relevant roles.
- Make operational policy values explicit variables (`report_stats`, user-directory policy, metrics).
- Preserve current MatrixRTC delayed-event and rate-limit settings unless upstream requirements change.
- Replace filesystem admin-user marker as the source of truth with an actual Synapse/user-state check.

## Firewall and IPv6

- Generate firewall rules from one variable model rather than duplicated hard-coded lists.
- Detect the active SSH port before enabling deny-incoming UFW policy.
- Apply the firewall baseline early enough to avoid exposing services during deployment.
- Treat IPv6 as an explicit supported/unsupported mode. An AAAA record with IPv6 disabled must be surfaced by preflight instead of being silently accepted.

## Bootstrap / operator UX

Add an operator-facing bootstrap/launcher around Ansible which performs, before deployment:

- Ubuntu/version and privilege checks;
- RAM/CPU/disk checks;
- internet/apt/dependency checks;
- external IPv4 detection with operator confirmation;
- domain/FQDN generation and confirmation;
- A/AAAA validation;
- local port conflict checks;
- NAT/direct-public detection;
- SSH port detection;
- existing installation/config/certificate detection;
- an explicit deployment plan and confirmation.

The bootstrap remains orchestration UX; Matrix service logic belongs in Ansible roles.

## Lifecycle workflows

Provide explicit backup, upgrade, rollback metadata, and destroy workflows. Destroy must require strong confirmation and must not silently remove the final backup.

## Validation gates

Before considering a release candidate complete:

1. syntax/static checks;
2. clean Ubuntu 24.04 deployment;
3. second Ansible run with no unintended changes;
4. `check.sh` desired-state review on the installed host;
5. reboot persistence test;
6. Certbot dry-run with deploy hooks;
7. incoming/outgoing federation checks;
8. legacy TURN relay check;
9. MatrixRTC local/federated audio+video calls including embedded TURN relay where possible;
10. backup/destroy/restore rehearsal on a disposable host;
11. final verifier with zero mandatory failures.

## Implementation status — `agent/roadmap-hardening`

### Implemented in code

- [x] Generic topology/subdomain model; no production domain/IP hard-coding in executable content.
- [x] Pinned controller/collection versions and fully pinned application image release tags.
- [x] CI registry validation for every application image, including linux/amd64 and linux/arm64 manifests.
- [x] Interactive bootstrap/launcher with persisted topology, input validation and preflight.
- [x] CI executes `bootstrap.sh --prepare-only` on an Ubuntu 24.04 runner.
- [x] CI executes NAT-mode preflight with real DNS A/AAAA resolution plus resource, port, routing and apt checks.
- [x] Direct-public/NAT distinction, exact local-IP checks and active SSH-session port detection.
- [x] DNS validation uses direct A/AAAA queries so IPv4-mapped NSS results cannot create false AAAA failures.
- [x] Explicit IPv6-disabled behavior with AAAA rejection.
- [x] Ansible roles use `ansible_facts[...]` with deprecated top-level fact injection disabled.
- [x] Docker and Nginx repositories use `deb822_repository`; deprecated `apt_repository` is statically rejected.
- [x] Deterministic PostgreSQL configuration, conservative tuning and major-version guard.
- [x] Synapse policy/metrics/federation wiring and database-backed admin reconciliation.
- [x] Partial-deploy recovery path with ephemeral `converge.sh --admin-password` secret handling.
- [x] Element permalink correction and Ketesa migration.
- [x] Production-proven LiveKit/JWT MatrixRTC security model and explicit embedded TURN relay range.
- [x] Independent hardened legacy Coturn contour with NAT mapping support.
- [x] ACME-safe Nginx bootstrap/production routing, SAN reconciliation, incomplete-lineage recovery and selective deploy hook.
- [x] Desired-state UFW including stale public 7880 removal.
- [x] Dedicated Fail2ban SSH baseline role with control-socket race handling.
- [x] Production-proven parameterized verifier and optional deep Certbot renewal test.
- [x] NAT-safe self-verification using loopback with correct Host/SNI rather than requiring hairpin NAT.
- [x] Bounded Docker container logging and no Docker-group privilege for the Matrix service account.
- [x] `converge.sh`, check-mode-aware `check.sh`, backup-first `upgrade.sh` and guarded `destroy.sh`.
- [x] Protected backup format with root-only artifacts; destructive destroy requires a full backup including Synapse media.
- [x] GitHub Actions static/YAML/shell/Ansible syntax gate.
- [x] CI renders and shell-validates verifier variants with federation both on and off.
- [x] Operator README and explicit restore runbook/contract.

### Deliberately deferred until integration testing

- [ ] Automated `restore.sh`. Backup format and restore sequence are documented, but automation is withheld until a full destroy/restore test succeeds.
- [ ] Matrix/Nginx Fail2ban filters. Add only after `fail2ban-regex` validation against real logs.
- [ ] Full IPv6 deployment mode.
- [ ] TURN/TLS on public TCP 443; requires a separate IP or deliberate L4/SNI design.

### Release gates still requiring a disposable/real Ubuntu host

- [ ] Full fresh Ubuntu 24.04 installation from `bootstrap.sh` with real project DNS and Let's Encrypt.
- [ ] Second `converge.sh` run with no unintended changes.
- [ ] `check.sh` on the installed host; confirm useful diff/no runtime false failures.
- [ ] Reboot and persistence verification.
- [ ] `verify.sh --deep` / Certbot staging renewal on the clean-host deployment.
- [ ] Incoming/outgoing federation checks when federation is enabled.
- [ ] Legacy Coturn authenticated relay test.
- [ ] Local and federated MatrixRTC audio/video calls; confirm embedded TURN relay availability.
- [ ] Full backup -> destroy -> restore rehearsal before exposing automated restore.
- [ ] Final verifier with zero mandatory failures on the release-candidate host.
