---
name: backup-ops
description: Run, check or repair the cluster's backups (Velero, CloudNativePG WAL and base backups, the etcd and OpenBao snapshot CronJobs, the offsite bucket and the TrueNAS pull). Use when the user says "trigger the base backups and velero backup now", "run the copy job once merged", "is the backup job done yet", "i merged it, verify it's working", "are my backups ok", "what time should truenas pull", "is there a runbook for truenas", "the backup policy is installed now", or when a backup alert fires. Covers triggering each kind on demand, where its result shows, the end-to-end check through offsite to TrueNAS, and the pitfalls that hid broken backups for weeks.
---

# backup-ops

What is backed up, the chain and the schedule are in
`docs/operations/backups.md` (`#current-state`, `#backup-chain`); restores
are there too. This skill is running them and proving they worked. A job
created by hand from a CronJob is the one imperative step allowed: it runs
what Git already defines.

## 1. Trigger on demand

| Backup | Trigger | Result |
| --- | --- | --- |
| Velero | `velero backup create --from-schedule velero-daily -n backup` | `kubectl -n backup get backups.velero.io` — `Completed`, not `PartiallyFailed`; `datauploads` `Completed` |
| CNPG base backup | `kubectl -n <ns> create -f -` of a `Backup` with `method: plugin`, `pluginConfiguration.name: barman-cloud.cloudnative-pg.io`, `cluster.name: <db>` | `kubectl get backups.postgresql.cnpg.io -A` — `completed`; the cluster's `ContinuousArchiving` condition `True` |
| etcd | `kubectl -n backup create job --from=cronjob/etcd-backup etcd-backup-manual-$(date +%s)` | Job `Complete`; object under `offsite/backup.etcd/` |
| OpenBao | `kubectl -n backup create job --from=cronjob/openbao-snapshot openbao-snapshot-manual-$(date +%s)` | Both containers' logs (`-c snapshot`, `-c upload`); object under `offsite/backup.openbao/` |

Confirm the CronJob names with `kubectl -n backup get cronjob` first. Delete
manual Jobs when done. `kubectl get backup` without the group resolves to
CNPG's kind, not Velero's: always say `backups.velero.io`.

## 2. Verify end to end

1. Every source wrote: list the prefixes and newest objects in `offsite`
   with the reader keys (`docs/operations/backups.md#checking-the-reader`).
2. Nothing failed silently: Velero `PartiallyFailed` and a data mover stuck
   in `Accepted` are failures (2026-09-14 to 09-27 lost every volume backup
   that way). Read `kubectl -n backup get datauploads` and the node-agent
   logs, not only the Backup's phase.
3. TrueNAS: the Cloud Sync task ran after the last cluster job and the ZFS
   snapshot after that (times in `docs/operations/backups.md#backup-chain`).
   The user reads the TrueNAS UI; ask for the task's last run and size.

## 3. After a change merges

When the user merged a backup change and says "verify it's working": wait
for the Argo CD sync (`argocd --core app get <app>`), trigger the affected
backup by hand (above) instead of waiting for the night, then check its
result and the object in `offsite`. A new database's bucket policy can lag
its first archive; `docs/platform/cloudnative-pg.md` has how to prove an idle
one archives.

## 4. Pitfalls

- A new backup source needs its own prefix user and the bucket policy
  (`docs/platform/cloudnative-pg.md#backing-up-a-database`); the offsite
  enforcement refuses a backup left out.
- Velero's repositories after a backup location change need re-initialising
  (`docs/platform/velero.md#re-initialising-the-repositories`).
- A stuck Velero maintenance Job blocks the next backup; see the Velero
  pitfalls.
- Docs gaps found on the way (a missing TrueNAS field, a schedule time) go
  into `docs/operations/backups.md` in the same PR.

## 5. Report

One row per backup kind: triggered or scheduled, result, object in
`offsite`, age. Then what the user must check on TrueNAS.
