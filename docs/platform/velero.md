---
description: "Velero backs up Kubernetes objects and PVC data nightly into the Ceph object store; the chart values that make it work against RGW."
---

# Velero

[Velero](https://velero.io/) backs up Kubernetes objects and, through CSI
snapshots and its data mover, the data in every PVC, nightly into a bucket in
the Ceph object store. What it covers, what it does not, and the restore
runbooks are in [Backups & Recovery](../operations/backups.md); this page is
the chart tuning.

## At a glance

| | |
| --- | --- |
| Namespace | `backup` |
| Depends on | [Rook-Ceph](rook-ceph.md) for the bucket claims and RBD snapshots |
| If it is down | Nightly backups stop; `VeleroBackupFailures` fires |
| Health check | `kubectl -n backup get backupstoragelocation` reads `Available` |
| Files | `payload/platform/velero/`, `payload/platform/backup/`, `payload/platform/snapshot-controller/` |

## Configuration

| Setting | Why |
| --- | --- |
| `checksumAlgorithm: ""` on the `BackupStorageLocation` | The AWS plugin's SDK sends a trailing checksum that Ceph RGW rejects with `XAmzContentSHA256Mismatch`; every upload fails without it |
| `velero-plugin-for-aws` init container | The plugin minor must match the chart's Velero appVersion; the chart's commented example is one line behind |
| `credentials.useSecret: false` with `extraEnvVars` | Keys come from the environment, fed by the `Secret` Rook writes for the `velero-bucket` claim; nothing is rendered into a file or Git |
| `defaultSnapshotMoveData`, `deployNodeAgent`, `uploaderType: kopia` | A CSI snapshot is a Ceph object in the same cluster; the data mover (run by the node agent) streams it into the bucket and deletes the snapshot |
| `volumeSnapshotLocation: []` | The chart's placeholder renders a `VolumeSnapshotLocation` the CRD rejects, failing every sync; Helm replaces lists, so the empty list removes it |
| `VolumeSnapshotClass` in `payload/platform/backup/volumesnapshotclass.yaml` | The `velero.io/csi-volumesnapshot-class` label is how Velero finds the class; without it volumes are skipped silently. `deletionPolicy: Delete`, because the durable copy is the data mover's |
| `snapshot-controller` Application | Neither kubeadm nor Rook installs the CSI snapshot controller or its CRDs, which must exist before the `VolumeSnapshotClass` applies, so it is its own Application |
| No `runAsNonRoot` on the `velero` container | Whether the plugin init container's copy into `/target` works non-root depends on the image's `USER`; confirm on a real backup first |
| Node agent `containerSecurityContext` | Root and capabilities stay because kopia reads every pod volume; escalation is off and seccomp is on |
| `prometheusRule` | `VeleroBackupFailures` (critical) and `VeleroBackupPartialFailures` (warning) |

## Usage

```bash
velero -n backup backup create manual-$(date +%s) --include-namespaces my-app
```

## Health check

```bash
kubectl -n backup get backupstoragelocation       # Available
kubectl -n backup get backups.velero.io           # Completed, not PartiallyFailed
kubectl -n backup get volumesnapshotclass rook-ceph-block
```

## Pitfalls

!!! warning "PartiallyFailed with no volume data"
    Velero found no labelled `VolumeSnapshotClass` and skipped every volume without an error. Check the class above exists and carries the label.
