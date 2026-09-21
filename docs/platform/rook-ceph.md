---
description: "Rook-Ceph distributed storage: block volumes, the S3 object store, monitoring, and how to request each."
---

# Rook-Ceph

Distributed block storage using [Rook](https://rook.io/) as the Kubernetes
operator for [Ceph](https://ceph.io/). It is the component that still has your
data after a node dies, and the one with the steepest learning curve. Check
`ceph status` more often than seems necessary and never ignore a `WARN`.

## At a glance

| | |
| --- | --- |
| Namespace | `rook-ceph` |
| Depends on | A raw `rook-osd` partition on every node, written at install time |
| If it is down | Every pod with a volume — `openbao` first, which turns a storage problem into a cluster problem |
| Health check | `make storage-check` — it binds a real PVC, which `ceph status` alone does not prove |
| UI | `rook.infra.k8s.wlkr.ch` |
| Files | `payload/platform/rook-ceph-operator/`, `payload/platform/rook-ceph-cluster/`, `payload/platform/rook-ceph/` |

## Configuration

### Disks

Each node has a raw partition labelled `rook-osd`, created by Ignition as the
**last** partition with whatever the root leaves, which Rook adds as an OSD;
data is replicated three ways across them. Its contents are wherever Ceph last
wrote them, so inserting a partition ahead of it shifts its offset and takes
the OSD data with it: changing the partition table is a
[reinstall](../operations/nodes.md), not an edit.

Ignition declares it with `format: none` and `wipe_filesystem: true`, because
`ceph-volume` reads the BlueStore *signature*, not the partition table, and a
reinstalled node that kept the old bytes would present an OSD from a cluster
that no longer exists. Ceph declines any device that already carries a
filesystem — the first thing to check when an OSD refuses to appear, see
[No OSDs after reprovisioning](#no-osds-after-reprovisioning).

### Storage classes

| StorageClass | Provides | Notes |
| --- | --- | --- |
| `rook-ceph-block` | RBD block volumes, cluster default | `ReadWriteOnce` only; `ReadWriteMany` needs CephFS, which is not enabled. Block PVs are deleted with their claim, and every Application prunes, so removing a PVC from Git removes the volume — see [Backups](../operations/backups.md) |
| `ceph-bucket` | S3 buckets from the object store | Reclaim `Retain`, so deleting a claim cannot delete the bucket |

### Object storage

A `CephObjectStore` provides an S3 endpoint at
`http://rook-ceph-rgw-object-store.rook-ceph.svc`, so backups and log chunks
live somewhere other than the block pool they protect. Two RGW instances keep
a node reboot from stalling a backup; the data pool is erasure coded 2+1 —
1.5x overhead rather than 3x, safe at `failureDomain: host` with six nodes.

### Monitoring

| Setting | Why |
| --- | --- |
| `cephClusterSpec.monitoring.enabled` | Turns on the mgr `prometheus` module and lets the operator maintain a `ServiceMonitor`. Without it a degraded pool, a down OSD or a near-full cluster is invisible to Prometheus, and a full Ceph cluster stops accepting writes |
| `monitoring.createPrometheusRules` | Ships Ceph's own alerting rules as a `PrometheusRule`. Its CRD comes from `prometheus-operator-crds` in `01-crds` so the kind exists when `04-storage` syncs — see [Monitoring](monitoring.md#crds) |
| `cephClusterSpec.cephConfig` `mgr/dashboard/*` | Sets the dashboard's Prometheus and Grafana links through the mon config store, which the operator reapplies on every reconcile, rather than a one-off `ceph dashboard set-*` Job |
| `mon_cluster_log_level: info` | The Ceph default is `debug`, and the mons copy the cluster log to stderr, so Rook's probes and the mgr's pgmap ticks became most of the mon log volume in [Loki](logging.md). Warnings and errors are unaffected |

Rook's Grafana dashboards are vendored in `grafana-dashboards.yaml` — see
[Monitoring &rarr; Dashboards](monitoring.md#dashboards).

### CSI driver

From Rook v1.20 the RBD driver is deployed by the ceph-csi-operator subchart,
which deploys nothing until an `OperatorConfig` and a `Driver` CR tell it what
to run. Neither chart ships them, so they live in `csi-driver.yaml`; without
them every PVC stays `Pending` on `ExternalProvisioning`.

| Setting | Why |
| --- | --- |
| `csi-driver.yaml` in `rook-ceph`, with `SkipDryRunOnMissingResource` | The CRs need the ceph-csi-operator's CRDs, which arrive with the operator chart |
| `OperatorConfig` pointing at the chart's image-set `ConfigMap` | A chart bump moves every sidecar and the cephcsi image together |
| `csi.<sidecar>.tag` pins in `rook-ceph-operator` | The sidecars Rook ships link a gRPC with an authorization bypass; the pins move ahead of the chart under `# renovate:` annotations and go once the chart catches up |
| Driver name `rook-ceph.rbd.csi.ceph.com` | Derived from the namespace and must match the StorageClasses' `provisioner`. RBD is the only driver; CephFS is not enabled |
| `csi-rbac.yaml` | The ceph-csi-operator creates no ServiceAccounts, and the names are fixed by `CSI_SERVICE_ACCOUNT_PREFIX` (empty here): `rbd-ctrlplugin-sa` and `rbd-nodeplugin-sa`. Without them the Deployment and DaemonSet exist but create no pods |

!!! warning "`csi` values on the operator chart do nothing, except the images"
    Helm accepts values a chart no longer reads. Driver settings go in `csi-driver.yaml`; only `csi.<sidecar>.tag` still renders into the image-set `ConfigMap`.

`csi-rbac.yaml` is the RBD half of the ceph-csi-operator's
`deploy/multifile/csi-rbac.yaml` at the version the rook-ceph chart pulls in;
Renovate does not see it. To refresh it:

1. Fetch and rename:

    ```bash
    curl -sSfL https://raw.githubusercontent.com/ceph/ceph-csi-operator/vX.Y.Z/deploy/multifile/csi-rbac.yaml \
      | sed -e 's/ceph-csi-operator-system/rook-ceph/' -e 's/ceph-csi-operator-//'
    ```

2. Keep only the rbd documents (CephFS, NFS and NVMe-oF are not enabled).
3. Re-indent the sequences for yamllint and prefix the cluster-scoped names.

### cephx keys stay on aes

Ceph 20 warns about the legacy `aes` cipher, and every key here is
deliberately on it: the kernel client krbd does not implement `aes256k`, so
CSI keys moved to it make every `rbd map` fail with
`map failed: (22) Invalid argument` while Ceph reports the rotation a success.
Two ciphers in one cluster trigger `AUTH_EMERGENCY_CIPHERS_SET` and buy no
security.

| Setting | Why |
| --- | --- |
| `keyType: aes` | krbd cannot use anything else |
| `keyRotationPolicy: KeyGeneration` | Changing `keyType` alone never rotates existing keys while the policy is `Disabled` |
| `keyGeneration` | Rotation only triggers on an increase, so a generation can never be lowered to undo one |
| `healthCheck.muteHealthWarning` | The `AUTH_*` warnings would otherwise keep the Application `Degraded` while the data is healthy. Temporary: remove once RBD moves to the userspace `rbd-nbd` mounter and the keys can leave `aes` |

## Usage

### Requesting a volume

Any workload can claim the default class:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-app-data
  namespace: my-app
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
```

Add `storageClassName: rook-ceph-block` to say so explicitly. A Deployment
with two replicas sharing one RWO PVC schedules one pod and leaves the other in
`ContainerCreating` with a multi-attach error.

### Requesting a bucket

Ask for an `ObjectBucketClaim`; Rook creates the bucket and writes a
`ConfigMap` (`BUCKET_NAME`, `BUCKET_HOST`, `BUCKET_PORT`) and a `Secret`
(`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) sharing the claim's name:

```yaml
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: my-app-bucket
  namespace: my-app
spec:
  generateBucketName: my-app
  storageClassName: ceph-bucket
```

## Health check

### Is storage ready?

```bash
make storage-check
```

Run it after the GitOps handover, before trusting anything that mounts a
volume: a `CephCluster` reports `Ready` with no OSDs, and a `StorageClass`
exists whether or not a CSI driver registered for it. The script walks the
chain in the order it breaks and stops at the first missing link:

| Check | What its absence means |
| --- | --- |
| `CephCluster` is `Ready` | the cluster Application has not synced yet |
| an OSD pod is `Running` | Rook took no disk — see [No OSDs after reprovisioning](#no-osds-after-reprovisioning) |
| `ceph health` is not `HEALTH_ERR` | Ceph itself is unwell; `HEALTH_WARN` is allowed through |
| the CSI driver is registered | no `Driver` CR, so the ceph-csi-operator deployed nothing |
| node plugin and provisioner are up | usually a ServiceAccount the DaemonSet cannot find |
| a 1 GiB PVC binds and is cleaned up | the only check that proves the other five |

The last one asks for a volume the way a workload would and waits `TIMEOUT`
seconds (120 by default); `NAMESPACE` and `CLASS` override where and what it
asks for.

## Pitfalls

!!! warning "Ceph warnings block the rollout"
    ArgoCD maps `HEALTH_WARN` to Degraded, so while Ceph warns no change reaches anything after `04-storage` — see [GitOps](../architecture/gitops.md).

## Recovery

### No OSDs after reprovisioning

A node whose `rook-osd` partition still carries a previous cluster's BlueStore
signature gets no OSD: the `CephCluster` reports `Ready` with `HEALTH_WARN`,
`kubectl -n rook-ceph get pods -l app=rook-ceph-osd` is empty, and the first
PVC — `data-openbao-0` on a fresh bootstrap — stays `Pending`.

!!! danger "The old cluster's data is already gone"
    Once the mons that held the cluster map left with the old control plane, those OSDs cannot be re-adopted by anything. Take a backup off the disks *before* a rebuild if you need one.

1. Confirm the cause:

    ```bash
    kubectl -n rook-ceph logs job/rook-ceph-osd-prepare-<node> | tail -3
    # skipping device "nvme0n1p2" because it contains a filesystem "ceph_bluestore"
    # skipping osd.3: "..." belonging to a different ceph cluster "..."
    ```

2. Rebuild the node; the [installer](../architecture/boot-process.md) wipes
   the disk:

    ```bash
    make reinstall LIMIT=<node>
    ```

3. If the node cannot be rebuilt now, wipe the partition by hand over SSH.
   `wipefs` removes the signature Rook skips on; the `dd` removes the
   BlueStore label behind it. Address it by label — the partition number
   differs between control-plane and worker nodes:

    ```bash
    sudo wipefs -a /dev/disk/by-partlabel/rook-osd
    sudo dd if=/dev/zero of=/dev/disk/by-partlabel/rook-osd bs=1M count=200
    ```

4. Restart the operator so it recreates the `osd-prepare` jobs:

    ```bash
    kubectl -n rook-ceph rollout restart deploy/rook-ceph-operator
    ```

5. Confirm an OSD per node and a binding PVC:

    ```bash
    make storage-check
    ```
