---
description: "Rook-Ceph distributed storage: block volumes, the S3 object store, monitoring, and how to request each."
---

# Rook-Ceph

Distributed block storage using [Rook](https://rook.io/) as the Kubernetes operator for [Ceph](https://ceph.io/).

## How It Works

Each node has a raw disk partition labeled `rook-osd` (created by Ignition at provisioning time). Rook detects these partitions and adds them as Ceph OSDs (Object Storage Daemons). Data is replicated across OSDs for redundancy.

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
would show up only if somebody ran `ceph status` by hand.

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
protect.

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

Any workload can request a PVC using the default storage class:

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
    `ReadWriteOnce` (RWO) is the supported access mode. `ReadWriteMany` (RWX) requires CephFS, which is not configured here.

## Directory Structure

```text
rook-ceph/             # Distributed Storage
├── application.yaml   # ArgoCD Application
├── operator.yaml      # Rook-Ceph operator
├── cluster.yaml       # CephCluster + CephBlockPool + CephObjectStore + StorageClasses
├── dashboard-config-job.yaml
└── httproute.yaml     # Rook dashboard route
```
