---
description: "What is backed up, what is not, how to take a backup by hand, and the restore runbooks for Velero, OpenBao, etcd and a full rebuild."
---

# Backups & Recovery

Every mechanism below is described by what it can get back, and by what it
cannot.

## Current state

| Data | Backed up | How |
| --- | --- | --- |
| Kubernetes objects | Nightly | [Velero](#velero) to the Ceph object store |
| Ceph RBD volumes (PVCs) | Nightly | Velero CSI snapshot, moved into the object store |
| Grafana dashboards | Nightly | Its PVC is covered by the above |
| etcd (raw) | Nightly | [CronJob](#etcd) to the object store |
| OpenBao secrets | Manual | [Raft snapshot](#openbao) |
| OpenBao unseal keys | Manual, off-cluster | Printed once at `bao operator init` |
| Prometheus metrics | **No** | Kept for the `retention` in `payload/platform/monitoring/application.yaml`, then gone |
| Everything in `payload/` | Yes | It is in Git |

## What is not covered

**Every automated backup lands in the same cluster's Ceph.** That protects
against operator error (a deleted PVC, a bad `prune`, a corrupted database) and
not against losing the cluster, because the backups go with it. Off-site
replication is the missing piece: RGW supports bucket replication and Velero a
second `BackupStorageLocation`, and neither is configured. Until then, disaster
recovery is **Git, the OpenBao unseal keys, and a copy of the latest OpenBao
and etcd snapshots kept off-cluster**; the nightly backups protect against
mistakes.

Velero's `PrometheusRule` (`VeleroBackupFailures` critical,
`VeleroBackupPartialFailures` warning) is what notices a backup that silently
stopped, and it only reaches whoever Alertmanager is configured to tell — see
[Monitoring](../platform/monitoring.md).

## Velero

[Velero](https://velero.io/) backs up Kubernetes objects and volume data
nightly. Chart values and why each is set are in
[Velero](../platform/velero.md).

| Property | Value |
| --- | --- |
| Schedule | The `schedules` block in `payload/platform/velero/application.yaml`: nightly, an hour after etcd so the two never contend for the object store, with a TTL of two weeks so a bad backup is noticed before the last good one expires |
| Scope | All namespaces except `kube-system` (rebuilt from Git, and large), minus `events` (they expire anyway) |
| Destination | S3 bucket `velero` in the [Ceph object store](../platform/rook-ceph.md) |
| Volume data | CSI snapshot, streamed into the bucket by the data mover (Kopia), snapshot deleted; the durable copy is the one in the bucket |

The `velero` CLI defaults to the `velero` namespace; this install is in
`backup`, so pass `-n backup` every time.

```bash
kubectl -n backup get backups.velero.io
kubectl -n backup get backupstoragelocation     # should be Available

velero -n backup backup create manual-$(date +%s) --include-namespaces my-app
```

Backups in `PartiallyFailed` with no volume data mean Velero found no
`VolumeSnapshotClass` labelled `velero.io/csi-volumesnapshot-class: "true"`
and skipped the volumes silently — check
`payload/platform/backup/volumesnapshotclass.yaml` is applied.

## etcd

Velero restores *through* the API server, which is no use with no API server
left (lost quorum, a corrupted data directory). For that a CronJob in
`payload/platform/backup/etcd-backup.yaml` snapshots etcd nightly, an hour
before Velero runs.

| Property | Value |
| --- | --- |
| Schedule | `payload/platform/backup/etcd-backup.yaml`: nightly, before Velero |
| Where it runs | Any control-plane node, on the host network: etcd listens on `127.0.0.1` and its client certs are on the node |
| Verification | `etcdutl snapshot status` before upload, so a truncated snapshot fails the job instead of replacing a good backup (`etcdctl snapshot status` was removed in etcd 3.6) |
| Destination | S3 bucket `etcd-backup`, separate from Velero's because it is restored by different means; the job keeps the newest snapshots and deletes the rest, the count is in the manifest |

Two settings there are easy to undo by accident: the DNS policy for a
host-network pod (the node's `resolv.conf` cannot resolve the RGW Service), and
the AWS CLI config file selecting path-style addressing (no environment
variable sets it).

```bash
kubectl -n backup get cronjob etcd-backup
kubectl -n backup logs job/<most-recent-job> -c upload
```

### Taking one by hand

```bash
kubectl -n kube-system exec -it etcd-<node> -- etcdctl \
  --cacert /etc/kubernetes/pki/etcd/ca.crt \
  --cert /etc/kubernetes/pki/etcd/server.crt \
  --key /etc/kubernetes/pki/etcd/server.key \
  snapshot save /var/lib/etcd/snapshot.db

kubectl cp kube-system/etcd-<node>:/var/lib/etcd/snapshot.db ./etcd-snapshot.db
```

Copy it off the cluster immediately.

## OpenBao

```bash
kubectl -n openbao exec -it openbao-0 -- bao operator raft snapshot save /tmp/snapshot.bao
kubectl -n openbao cp openbao-0:/tmp/snapshot.bao ./openbao-snapshot.bao
```

The snapshot holds all KV data plus policies, roles and mounts. It does **not**
hold the unseal keys and is useless without them — see
[OpenBao](../platform/openbao.md).

## Ceph volumes

PVCs are covered by [Velero](#velero). Ceph's own replication is not a backup:
it spreads each block across OSDs, which survives a disk or node failing and
nothing else. Almost every Application runs with `prune: true` (`kube-vip` and
`security` are the deliberate exceptions), so removing a
`PersistentVolumeClaim` from Git deletes the volume.

## Restoring

The commands are the upstream-documented ones for the versions pinned in
`payload/platform/`; the etcd sequence has not been exercised on this cluster.

### Velero restore

1. Find the backup.

    ```bash
    velero -n backup backup get
    ```

2. Restore from it, scoped to what was lost; without `--include-namespaces`
   everything in the backup is restored, and existing objects are skipped, not
   overwritten.

    ```bash
    velero -n backup restore create --from-backup velero-daily-<timestamp> \
      --include-namespaces my-app
    ```

3. Watch it finish and read the warnings.

    ```bash
    velero -n backup restore describe <restore-name>
    velero -n backup restore logs <restore-name>
    ```

### OpenBao snapshot restore

Needs an initialised, unsealed cluster and the root token. `-force` is what
lets a snapshot from a *different* cluster (a fresh `bao operator init`) load;
afterwards the pods seal and want the snapshot's keys, not the new cluster's.

1. Copy the snapshot in.

    ```bash
    kubectl -n openbao cp ./openbao-snapshot.bao openbao-0:/tmp/snapshot.bao
    ```

2. Restore it.

    ```bash
    kubectl -n openbao exec -it openbao-0 -- \
      env BAO_TOKEN=<root-token> bao operator raft snapshot restore -force /tmp/snapshot.bao
    ```

3. Unseal every replica with the snapshot's key shares and confirm.

    ```bash
    make bao-unseal
    kubectl -n openbao exec openbao-0 -- bao status   # Sealed: false
    ```

### etcd restore

Every etcd member restores from the same snapshot, then all three static pods
come back together. Flatcar ships no `etcdutl`, so run it from the etcd image
the CronJob uses. Upstream:
[Restoring an etcd cluster](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/#restoring-an-etcd-cluster).

1. Copy the snapshot to every control-plane node as `/var/lib/etcd-restore/snapshot.db`.
2. On every control-plane node, stop the API server and etcd by moving their
   static pod manifests out, and wait until neither container is listed.

    ```bash
    sudo mkdir -p /root/manifests-stopped
    sudo mv /etc/kubernetes/manifests/{etcd,kube-apiserver}.yaml /root/manifests-stopped/
    sudo crictl ps --name 'etcd|kube-apiserver'    # empty
    ```

3. On every control-plane node, restore into a new data directory with the
   `--name`, `--initial-cluster` and `--initial-advertise-peer-urls` values
   from that node's `/etc/kubernetes/manifests/etcd.yaml` (the image tag is in
   `payload/platform/backup/etcd-backup.yaml`).

    ```bash
    sudo ctr -n k8s.io run --rm \
      --mount type=bind,src=/var/lib/etcd-restore,dst=/restore,options=rbind:rw \
      registry.k8s.io/etcd:<tag> etcd-restore \
      etcdutl snapshot restore /restore/snapshot.db --data-dir /restore/etcd \
        --name <node> --initial-cluster <name=https://ip:2380,...> \
        --initial-advertise-peer-urls https://<node-ip>:2380
    ```

4. Swap the data directory in, keeping the old one and its ownership.

    ```bash
    sudo mv /var/lib/etcd /var/lib/etcd.bak
    sudo mv /var/lib/etcd-restore/etcd /var/lib/etcd
    sudo chown -R --reference=/var/lib/etcd.bak /var/lib/etcd
    ```

5. Put the manifests back on every node, then confirm.

    ```bash
    sudo mv /root/manifests-stopped/*.yaml /etc/kubernetes/manifests/
    kubectl get nodes
    kubectl -n argocd get applications     # ArgoCD reconverges from Git
    ```

### Rebuild from scratch

1. Have the Git repository, the OpenBao unseal keys and root token, and an
   OpenBao snapshot; an etcd snapshot is optional and skips re-issuing
   certificates.
2. Provision the cluster per the [Quickstart](../quickstart.md) from step 1.
3. When every `ExternalSecret` is Degraded, either `make bao-init` and restore
   the OpenBao snapshot as above, or `make bao-init` and re-enter every secret
   with `make bao-secrets`.
4. Once the platform is Synced and Healthy, restore workloads with a Velero
   restore from the copied-off backup.
