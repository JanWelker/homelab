---
description: "What is backed up, what is not, and how to snapshot etcd and OpenBao before you need them."
---

# Backups & Recovery

## Current state

| Data | Backed up | How |
| --- | --- | --- |
| Kubernetes objects | Nightly, 02:00 | [Velero](#velero) to the Ceph object store, 14 day TTL |
| Ceph RBD volumes (PVCs) | Nightly, 02:00 | Velero CSI snapshot, moved into the object store |
| Grafana dashboards | Nightly | Its PVC is covered by the above |
| etcd (raw) | Nightly, 01:00 | [CronJob](#etcd) to the object store, last 14 kept |
| OpenBao secrets | Manual | [Raft snapshot](../platform/openbao.md#backups) |
| OpenBao unseal keys | Manual, off-cluster | Printed once at `bao operator init` |
| Prometheus metrics | **No** | 10 day retention, then gone |
| Everything in `payload/` | Yes | It is in Git; that is the point of GitOps |

## What is not covered

Alerting is the other half of a backup: a backup that silently stopped running
is indistinguishable from one that works until you need it. Velero's
`PrometheusRule` covers that, but only reaches whoever Alertmanager is
configured to tell — see
[Monitoring](../platform/monitoring.md) for the state of that.

The bigger gap is where the backups land.

**Every automated backup here goes into the same cluster's Ceph.** That protects
against the failures that actually happen — a deleted PVC, a bad `prune`, a
corrupted database, a workload that ate its own data. It does not protect
against losing the cluster, because the backups go with it.

Off-site replication is the missing piece. RGW supports bucket replication and
Velero supports a second `BackupStorageLocation`, so the shape of the fix is
known; neither is configured. Until then, treat the OpenBao unseal keys plus
Git as the real disaster-recovery story and these backups as protection against
mistakes rather than against the building burning down.

## Velero

[Velero](https://velero.io/) backs up Kubernetes objects and volume data
nightly at 02:00, keeping 14 days.

| Property | Value |
| --- | --- |
| Schedule | `0 2 * * *`, TTL `336h` |
| Scope | All namespaces except `kube-system`, minus `events` |
| Destination | S3 bucket `velero` in the [Ceph object store](../platform/rook-ceph.md) |
| Volume data | CSI snapshot, then moved into the bucket by the data mover (Kopia) |

`kube-system` is excluded because it is reconstructed from Git on the next sync
and is large. `events` are excluded because they expire anyway.

### Why the data mover matters

A CSI snapshot on its own is a Ceph object. Backing up a PVC by snapshotting it
would leave the only copy inside the same Ceph cluster the backup exists to
survive — protection against a deleted PVC, but not against a broken pool. With
`defaultSnapshotMoveData`, Velero takes the snapshot, streams the data out to
the object store, and deletes the snapshot. The durable copy is the one in the
bucket.

This is why `deployNodeAgent` is on: the node agent is what reads the snapshot
and does the streaming.

### Two settings that are not optional here

```yaml
checksumAlgorithm: ""      # on the BackupStorageLocation config
```

The AWS plugin's `aws-sdk-go-v2` sends a trailing checksum that Ceph RGW rejects
with `api error XAmzContentSHA256Mismatch`, so **every upload fails** without
this. The plugin's own README lists Ceph S3 as needing it.

```yaml
image: velero/velero-plugin-for-aws:v1.14.2
```

Plugin `v1.14.x` pairs with Velero `v1.18.x`, this chart's appVersion. The
chart's commented example still shows `v1.13.1`, which is the v1.17 line.

### Using it

```bash
kubectl -n backup get backups.velero.io
kubectl -n backup get backupstoragelocation     # should be Available

# On demand
velero backup create manual-$(date +%s) --include-namespaces my-app

# Restore
velero restore create --from-backup velero-daily-20260905020000
```

If backups sit in `PartiallyFailed`, check that a `VolumeSnapshotClass` labelled
`velero.io/csi-volumesnapshot-class: "true"` exists — without it Velero finds no
class for the RBD driver and skips volumes silently.

### Alerting

Velero ships a `PrometheusRule` here: `VeleroBackupFailures` (critical) and
`VeleroBackupPartialFailures` (warning). A backup that silently stopped running
is the failure mode this exists to prevent.

## etcd

Velero restores objects *through the API server*. That is the wrong tool for the
case where there is no API server left to restore through — lost quorum, a
corrupted data directory, three dead control-plane nodes. For that you need the
etcd data itself, which Velero does not capture.

A CronJob takes one nightly at 01:00, an hour before Velero runs:

| Property | Value |
| --- | --- |
| Schedule | `0 1 * * *` |
| Where it runs | Any control-plane node — `hostNetwork`, since etcd listens on `127.0.0.1` and its client certs are on the node |
| Verification | `etcdutl snapshot status` before upload, so a truncated snapshot fails the job instead of quietly replacing a good backup |
| Destination | S3 bucket `etcd-backup`, newest 14 kept |

!!! note "`etcdctl snapshot status` no longer exists"
    It was removed in etcd 3.6. The verification step uses `etcdutl`, which
    ships in the same image.

```bash
kubectl -n backup get cronjob etcd-backup
kubectl -n backup logs job/<most-recent-job> -c upload
```

### Taking one by hand

Take a snapshot from a control-plane node:

```bash
kubectl -n kube-system exec -it etcd-<node> -- etcdctl \
  --cacert /etc/kubernetes/pki/etcd/ca.crt \
  --cert /etc/kubernetes/pki/etcd/server.crt \
  --key /etc/kubernetes/pki/etcd/server.key \
  snapshot save /var/lib/etcd/snapshot.db

kubectl cp kube-system/etcd-<node>:/var/lib/etcd/snapshot.db ./etcd-snapshot.db
```

Copy it off the cluster. A snapshot stored only on a Ceph PVC does not survive
the failure it exists for.

Restoring is `etcdctl snapshot restore` into a fresh data directory on a stopped
control plane, then restarting kubelet — see the
[upstream kubeadm documentation](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/#restoring-an-etcd-cluster).

## OpenBao

```bash
kubectl -n openbao exec -it openbao-0 -- bao operator raft snapshot save /tmp/snapshot.bao
kubectl -n openbao cp openbao-0:/tmp/snapshot.bao ./openbao-snapshot.bao
```

The snapshot contains all KV data plus policies, roles and mounts. It does
**not** contain the unseal keys, and it is useless without them. Details in
[OpenBao &rarr; Backups](../platform/openbao.md#backups).

## Ceph volumes

There is no volume backup. Rook-Ceph replicates across OSDs, which protects
against a disk or node failure but not against deletion, corruption, or a bad
`prune`. Every Application here runs with `prune: true`, so removing a
`PersistentVolumeClaim` from Git deletes the volume.

[Velero](#velero) now covers this: PVCs are snapshotted nightly and the data is
moved into the object store, so a deleted PVC is recoverable. What is still open
is off-cluster replication — see
[What is not covered](#what-is-not-covered). RBD mirroring to a
second cluster would close it.

## Rebuilding from scratch

What you need, in order:

1. The Git repository — all platform and workload manifests.
2. The OpenBao unseal keys and root token — without these the restored OpenBao
   is an encrypted brick.
3. An OpenBao raft snapshot, or the willingness to re-enter every secret.
4. Optionally an etcd snapshot, to skip re-issuing certificates and waiting for
   the platform to re-converge.

The rebuild itself is the [Quickstart](../quickstart.md) from step 1. Because
the platform is declarative, the cluster converges back to its documented state
once ArgoCD points at the repository and OpenBao is unsealed.
