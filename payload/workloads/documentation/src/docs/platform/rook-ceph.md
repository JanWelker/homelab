# rook-ceph

Distributed storage using Ceph.

## Components

- **Storage**: Raw partition OSD (`/dev/disk/by-partlabel/rook-osd`).
- **Dashboard**: Ceph UI at `rook.infra.k8s.wlkr.ch`.
- **StorageClass**: `rook-ceph-block` (default, RBD-based).

## Directory Structure

```text
rook-ceph/             # Distributed Storage
├── operator.yaml      # Rook-Ceph operator (v1.16.1)
├── cluster.yaml       # CephCluster + CephBlockPool + StorageClass
├── dashboard-config-job.yaml
└── httproute.yaml     # Rook dashboard route
```
