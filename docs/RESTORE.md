# Restore status and backup format

Automated restore is intentionally **not enabled yet**. The backup format is now
sufficiently structured to build restore automation, but restoring a Matrix
identity/database/media set is a destructive operation and must first be proven
on a disposable Ubuntu 24.04 host.

## Backup contents

Each `/var/backups/matrix-deploy/<timestamp>/` contains:

- `MANIFEST.txt`;
- `synapse.pgdump` — PostgreSQL custom-format logical dump;
- `config-state.tar.gz` — deployment topology, generated secrets, Synapse
  configuration/signing key, app configuration, Nginx/Coturn/Fail2ban state,
  Let's Encrypt state, and optionally the Synapse media store;
- `docker-inspect.json`;
- `docker-images.txt`;
- `ufw-status.txt`.

The raw PostgreSQL data directory is not backed up. Restore must initialize the
pinned PostgreSQL major version and load `synapse.pgdump` with `pg_restore`.

## Media requirement

For disaster recovery after `destroy.sh`, use only a backup whose manifest says:

```text
media_store_included=true
```

`destroy.sh` enforces creation of such a backup before removing the deployment.
A normal `backup.sh` or pre-upgrade backup can legitimately say
`media_store_included=false` because the live media directory is not removed by a
normal converge/upgrade.

## Planned restore sequence

The integration-tested restore workflow should perform these stages, in order:

1. validate the backup manifest and requested Matrix server name;
2. require a clean/matching Ubuntu 24.04 target and compatible repository version;
3. prepare the pinned Ansible environment (`bootstrap.sh --prepare-only`);
4. restore `/etc/matrix-deploy/deployment.yml`, source secrets, signing key,
   certificate state and media/config files from `config-state.tar.gz`;
5. deploy only host baseline + Docker + Nginx + an empty PostgreSQL instance;
6. restore `synapse.pgdump` into the empty Synapse database with explicit
   ownership/clean semantics;
7. deploy Synapse and the remaining application/TURN roles;
8. run standard and deep verification;
9. verify login, federation, legacy TURN and MatrixRTC calls before declaring the
   restore complete.

Do not replace this sequence with a raw copy of PostgreSQL's data directory. The
playbook has a PostgreSQL major-version guard specifically to prevent implicit
major upgrades or incompatible data-directory starts.

## Why there is no `restore.sh` yet

A syntactically correct restore script can still destroy identity, signing-key,
media or database consistency if its ordering is wrong. This repository will add
`restore.sh` only after the sequence above has passed a clean-host destroy/restore
integration test using a full backup.
