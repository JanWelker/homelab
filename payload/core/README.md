# Core Infrastructure

This directory contains **core infrastructure components** that are applied after initial cluster bootstrap via `make install-core`.

## Components

| File/Directory | Description | Managed By |
|----------------|-------------|------------|
| `cilium/` | Cilium CNI configuration (kube-proxy replacement, WireGuard, Ingress, L2) | **Makefile** (pre-ArgoCD) |
| `cert-manager.yaml` | Cert-Manager CRDs and deployment manifest | ArgoCD |
| `cluster-issuer.yaml` | Let's Encrypt **staging** ClusterIssuer (for testing) | ArgoCD |
| `cluster-issuer-prod.yaml` | Let's Encrypt **production** ClusterIssuer | ArgoCD |
| `argocd-cert.yaml` | Certificate resource for ArgoCD Ingress TLS | ArgoCD |
| `rook-ceph/` | Rook-Ceph storage operator and cluster configuration | ArgoCD |

### Cilium (Bootstrap)

The `cilium/` directory is **excluded from ArgoCD** because Cilium must be installed before ArgoCD exists:

| File | Description |
|------|-------------|
| `cilium-values.yaml` | Helm values for Cilium (includes global HTTPS redirect) |
| `cilium-pool.yaml` | `CiliumLoadBalancerIPPool` and `CiliumL2AnnouncementPolicy` |

### Rook-Ceph Storage

The `rook-ceph/` directory contains ArgoCD Applications for persistent storage:

| File | Description |
|------|-------------|
| `operator.yaml` | Rook-Ceph operator (Helm chart v1.16.1) |
| `cluster.yaml` | CephCluster + CephBlockPool + StorageClass |

**Configuration:**

- **Storage**: Raw partition OSD (`/dev/disk/by-partlabel/rook-osd`)
- **Replication**: Single replica (for single-node clusters)
- **StorageClass**: `rook-ceph-block` (default, RBD-based)
- **Use case**: PVCs for apps like Home Assistant, Nextcloud, etc.

## Usage

### Bootstrap (before ArgoCD)

```bash
make install-core  # Installs Cilium + Cert-Manager
make install-argo  # Installs ArgoCD
```

### GitOps (after ArgoCD)

The `core-infrastructure` ArgoCD Application syncs this directory, **excluding**:

- `cilium/` (bootstrapped via Makefile)
- `README.md` (not a K8s manifest)

Rook-Ceph is deployed automatically via sync waves (`-2` for operator, `-1` for cluster).
