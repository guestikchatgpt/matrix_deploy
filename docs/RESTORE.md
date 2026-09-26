# Restore status and backup format

**English** | [Русский](ru/RESTORE.md)

Automated restore is deliberately **not enabled yet**. The backup format is
already structured enough to build restore automation on, but restoring the
Matrix identity/database/media is a destructive operation that must first be
proven on a disposable Ubuntu 24.04 host.

## Backup contents

Each `/var/backups/matrix-deploy/<timestamp>/` directory contains:

- `MANIFEST.txt`;
- `synapse.pgdump` — a logical PostgreSQL dump in custom format;
- `config-state.tar.gz` — the deployment topology, the runtime version lock,
  generated secrets, Synapse configuration/signing key, application
  configuration, Nginx/Coturn/Fail2ban state, Let's Encrypt state and,
  optionally, the Synapse media store;
- `docker-inspect.json`;
- `docker-images.txt`;
- `ufw-status.txt`.

In particular, the `/etc/matrix-deploy` backup must keep both files:

```text
/etc/matrix-deploy/deployment.yml
/etc/matrix-deploy/versions.yml
```

`versions.yml` is the exact set of application images the backed-up
installation was running. During disaster recovery it must be restored
together with the topology, rather than re-selecting the latest stable
releases during the restore itself. The restored deployment can be upgraded to
newer stable versions later with a separate `upgrade.sh`, after the restore has
been verified.

The raw PostgreSQL data directory is not included in the backup. A restore
must initialize the same allowed PostgreSQL major version specified in the
restored version lock/policy and load `synapse.pgdump` with `pg_restore`.

## Media requirement

For disaster recovery after `destroy.sh`, only use a backup whose manifest
says:

```text
media_store_included=true
```

`destroy.sh` always creates such a backup before removing the deployment. A
regular `backup.sh` or pre-upgrade backup may well contain
`media_store_included=false`, because a normal converge/upgrade never deletes
the live media directory.

## Planned restore sequence

A restore workflow proven by an integration test must perform the following
stages, in exactly this order:

1. verify the backup manifest, the recorded Matrix server name and the
   presence of the runtime version lock;
2. require a clean/compatible Ubuntu 24.04 target and a compatible repository
   version;
3. prepare the pinned Ansible environment (`bootstrap.sh --prepare-only`);
4. restore `/etc/matrix-deploy/deployment.yml`,
   `/etc/matrix-deploy/versions.yml`, the original secrets, signing key,
   certificate state and media/config files from `config-state.tar.gz`;
5. check that the exact images from the restored lock are still available in
   the registry; **do not refresh the lock automatically**;
6. deploy only the host baseline + Docker + Nginx + an empty PostgreSQL
   instance of the required major version;
7. restore `synapse.pgdump` into the empty Synapse database with `pg_restore`,
   with explicitly defined ownership/clean semantics;
8. deploy Synapse and the remaining application/TURN roles with the exact image
   refs from the restored lock;
9. run regular and deep verification;
10. check login, federation, classic TURN and MatrixRTC calls before declaring
    the restore complete.

This sequence must not be replaced by simply copying the raw PostgreSQL data
directory. The playbook deliberately includes a PostgreSQL major-version guard
that prevents implicit major upgrades or starting an incompatible data
directory.

Disaster recovery must also not be used as a hidden upgrade. A restore must
first reproduce the saved state, and only after it has been verified can
`upgrade.sh` be run separately to re-resolve the latest stable upstream
releases.

## Why there is no `restore.sh` yet

Even a syntactically correct restore script can break the consistency of the
identity, signing key, media, database or application version set if the
stages run in the wrong order. `restore.sh` will be added to this repository
only after the sequence above passes a clean-host destroy/restore integration
test using a full backup.
