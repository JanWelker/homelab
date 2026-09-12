---
description: "Rook-Ceph distributed storage: block volumes, the S3 object store, monitoring, and how to request each."
---

# Rook-Ceph

Distributed block storage using [Rook](https://rook.io/) as the Kubernetes operator for [Ceph](https://ceph.io/).

Ceph is the component in this cluster with the steepest learning curve and the
longest memory. It is also the one that will still have your data after a node
dies, which is why it is here. Treat `ceph status` the way a sysadmin treats
`dmesg`: check it more often than seems necessary, and never ignore a `WARN` on
the grounds that everything still appears to work.

## How It Works

Each node has a raw disk partition labeled `rook-osd` (created by Ignition at provisioning time). Rook detects these partitions and adds them as Ceph OSDs (Object Storage Daemons). Data is replicated across OSDs for redundancy.

"Raw" is load-bearing there. Ceph wants the block device, not a filesystem on it, and it will politely decline anything that already has one — which is the correct behaviour and also the first thing to check when an OSD refuses to appear. On a node that has been provisioned before, the thing already on it is usually the last cluster's OSD: see [No OSDs after reprovisioning](#no-osds-after-reprovisioning).

## Components

- **StorageClass**: `rook-ceph-block` is set as the cluster default. Any `PersistentVolumeClaim` without an explicit `storageClassName` will use it.
- **StorageClass**: `ceph-bucket` provisions S3 buckets from the object store — see [Object storage](#object-storage).
- **Dashboard**: Ceph management UI at [https://rook.infra.k8s.wlkr.ch](https://rook.infra.k8s.wlkr.ch).
- **Metrics**: the Ceph mgr `prometheus` module is enabled and the operator maintains a `ServiceMonitor`, so cluster health reaches Prometheus.

## Monitoring

`cephClusterSpec.monitoring.enabled` is `true`, which turns on the mgr
prometheus module and lets the operator maintain a `ServiceMonitor`. Without it
no Ceph metric reaches Prometheus at all, and storage becomes the one thing the
monitoring stack cannot see: a degraded pool, a down OSD, or a near-full cluster
would show up only if somebody ran `ceph status` by hand. A full Ceph cluster
does not degrade gracefully — it stops accepting writes, and every workload
finds out simultaneously. This is not a metric to leave unwatched.

`monitoring.createPrometheusRules` ships Ceph's own alerting rules alongside it
(`CephClusterErrorState`, `CephOSDDown`, `CephPGsUnhealthy`, the near-full
warnings, and the rest).

!!! note "Sync ordering"
    The rules render as a `PrometheusRule`, whose CRD arrives with
    kube-prometheus-stack at sync-wave `1` — after this Application at `-1`. On
    a **fresh** bootstrap the first sync therefore runs before the CRD exists,
    so the Application carries `SkipDryRunOnMissingResource=true` and ArgoCD
    retries until kube-prometheus-stack has landed. On an existing cluster the
    CRD is already there and this never comes up.

## Object storage

A `CephObjectStore` provides an S3-compatible endpoint inside the cluster,
served by two RGW instances. It exists so that backups and log chunks have
somewhere to live that is not a PVC on the same block pool they are meant to
protect. Same cluster, different pool — half a step, but a real one.

| Property | Value |
| --- | --- |
| Store | `object-store` |
| StorageClass | `ceph-bucket` |
| Metadata pool | Replicated, size 3 |
| Data pool | Erasure coded 2+1 — 1.5x overhead rather than 3x |
| Reclaim policy | `Retain`, so deleting a claim cannot delete the bucket |
| Endpoint | `http://rook-ceph-rgw-object-store.rook-ceph.svc` |

### Requesting a bucket

Ask for an `ObjectBucketClaim` rather than a PVC. Rook creates the bucket and
writes the endpoint and credentials into a `ConfigMap` and `Secret` that share
the claim's name:

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

The resulting `Secret` holds `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`;
the `ConfigMap` holds `BUCKET_NAME`, `BUCKET_HOST` and `BUCKET_PORT`.

## Requesting Storage

Any workload can request a PVC using the default storage class. Note the words
"default" and "Retain" are doing different jobs here: block PVCs are deleted
with their claim, and every Application in this repo syncs with `prune: true`.
Removing a `PersistentVolumeClaim` from Git removes the volume. Ask Velero how
it feels about that in [Backups & Recovery](../operations/backups.md).

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

To use it explicitly:

```yaml
  storageClassName: rook-ceph-block
```

!!! note
    `ReadWriteOnce` (RWO) is the supported access mode. `ReadWriteMany` (RWX) requires CephFS, which is not configured here — so a Deployment with two replicas sharing one PVC will schedule one pod and leave the other stuck in `ContainerCreating`, wondering aloud about a multi-attach error.

## Is storage ready?

```bash
make storage-check
```

Run it after the GitOps handover and before trusting anything that mounts a
volume. The sync waves already put Rook ahead of every such workload, and that
is not the same question: a `CephCluster` reports `Ready` with mons and mgrs up
while having no OSDs to store data on, and a `StorageClass` exists whether or
not a CSI driver ever registered for its provisioner. Both failures leave the
apps at later waves running and their pods `Pending`, several layers away from
the cause.

The script walks the chain instead, in the order it breaks, and stops at the
first missing link with the command that explains it:

| Check | What its absence means |
| --- | --- |
| `CephCluster` is `Ready` | the cluster Application has not synced yet |
| an OSD pod is `Running` | Rook took no disk — see [No OSDs after reprovisioning](#no-osds-after-reprovisioning) |
| `ceph health` is not `HEALTH_ERR` | Ceph itself is unwell; `HEALTH_WARN` is allowed through |
| the CSI driver is registered | no `Driver` CR, so the ceph-csi-operator deployed nothing |
| node plugin and provisioner are up | usually a ServiceAccount the DaemonSet cannot find |
| a 1 GiB PVC binds and is cleaned up | the only check that proves the other five |

The last one is the point of the exercise: it asks for a volume the same way a
workload would, waits up to `TIMEOUT` seconds (120 by default) for it to bind,
and deletes it again. `NAMESPACE` and `CLASS` override where it asks and which
`StorageClass` it asks for.

## No OSDs after reprovisioning

Reinstalling the nodes does not give Ceph empty disks back. Butane creates the
`rook-osd` partition only when it is absent and deliberately leaves it
unformatted, so a rebuild onto the same hardware inherits the previous
cluster's OSDs — and Rook will not touch an OSD that belongs to a cluster it
does not know:

```console
$ kubectl -n rook-ceph logs job/rook-ceph-osd-prepare-odin | tail -3
skipping device "nvme0n1p2" because it contains a filesystem "ceph_bluestore"
skipping osd.3: "629a6636-..." belonging to a different ceph cluster "1c569bbb-..."
skipping OSD configuration as no devices matched the storage settings for this node "odin"
```

The `CephCluster` reports `Ready` with `HEALTH_WARN` and the mons and mgrs come
up, so the cluster looks alive; it simply has nowhere to put data. What you
notice instead is the first workload that wants a volume:

```console
$ kubectl -n rook-ceph get pods -l app=rook-ceph-osd
No resources found in rook-ceph namespace.
$ kubectl -n openbao get pvc
data-openbao-0   Pending   rook-ceph-block
$ kubectl -n openbao describe pod openbao-0 | tail -1
0/4 nodes are available: pod has unbound immediate PersistentVolumeClaims.
```

which in a fresh bootstrap means [quickstart](../quickstart.md) step 11 cannot
start: `bao operator init` has no pod to exec into.

!!! danger "This destroys the old cluster's data"
    Wiping the partition is not recoverable, and neither is declining to: once
    the mons that held the cluster map are gone with the old control plane,
    those OSDs cannot be re-adopted by anything. Take a backup off the disks
    first if you need one, then wipe with your eyes open.

Clear the partition on every node and let the operator try again:

```bash
make wipe-osd                    # every host in k8s_nodes
make wipe-osd LIMIT=odin,thor    # only these
```

It prints the hosts it is about to wipe and waits for you to type `WIPE`.
There is no flag to skip that, and it refuses to run without a terminal to ask
at: the partitions do not come back, and neither does what was on them.

What it runs, per node, is `wipefs -a` followed by a 200 MiB `dd` over
`/dev/disk/by-partlabel/rook-osd`. `wipefs` removes the signature that made
Rook skip the device; the `dd` removes the BlueStore label and superblock
behind it, which is what `ceph-volume raw list` reads. Addressing the partition
by label rather than by name matters: the control-plane nodes present it as
`nvme0n1p2` and the worker as `sda2`, and nothing at that path can be the
`containerd` partition. Ansible runs it with `-m raw` for the same reason
`kubeconfig.yaml` does — Flatcar ships no `/usr/bin/python3`, so every other
module fails with `rc=127`.

It then restarts the operator, which recreates the `osd-prepare` jobs. An OSD
appears per node and the pending PVCs bind on the next provisioning attempt;
`make storage-check` is the way to confirm that rather than assume it.

## Directory Structure

```text
rook-ceph/             # Distributed Storage
├── application.yaml   # ArgoCD Application
├── operator.yaml      # Rook-Ceph operator
├── cluster.yaml       # CephCluster + CephBlockPool + CephObjectStore + StorageClasses
└── httproute.yaml     # Rook dashboard route
```
