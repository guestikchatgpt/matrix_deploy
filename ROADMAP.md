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
- Ketesa currently documents the `latest` container channel as the supported drop-in path; treat it as an explicit exception until an appropriate immutable production tag/digest policy is selected.

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
4. reboot persistence test;
5. Certbot dry-run with deploy hooks;
6. incoming/outgoing federation checks;
7. legacy TURN relay check;
8. MatrixRTC local/federated audio+video calls including embedded TURN relay where possible;
9. final verifier with zero mandatory failures.
