# Core Infrastructure

This directory contains **core infrastructure components** managed via ArgoCD GitOps.

## Directory Structure

```text
core/
├── cert-manager/          # TLS Certificate Management
│   ├── application.yaml   # ArgoCD Application (Helm chart)
│   ├── cluster-issuers.yaml # Let's Encrypt staging + prod issuers
│   ├── certificates.yaml  # All Certificate resources
│   └── route53-credentials.yaml.template
│
├── cilium/                # CNI + Gateway API Controller
│   ├── application.yaml   # ArgoCD Application (Helm v1.18.5)
│   ├── values.yaml        # Helm values
│   ├── lb-pools.yaml      # CiliumLoadBalancerIPPool + L2 Policy
│   ├── rbac-gateway-fix.yaml # RBAC fix for Gateway API
│   └── httproute.yaml     # Hubble UI route
│
├── gateway-api/           # Gateway API Resources
│   ├── crds.yaml          # ArgoCD Application for CRDs (v1.2.0)
│   └── gateways.yaml      # apps-gateway + infra-gateway
│
├── monitoring/            # Observability Stack
│   ├── application.yaml   # kube-prometheus-stack
│   └── httproute.yaml     # Grafana route
│
└── rook-ceph/             # Distributed Storage
    ├── operator.yaml      # Rook-Ceph operator (v1.16.1)
    ├── cluster.yaml       # CephCluster + CephBlockPool + StorageClass
    ├── dashboard-config-job.yaml
    └── httproute.yaml     # Rook dashboard route
```

## Components

### cert-manager/

TLS certificate automation via Let's Encrypt:

- **ClusterIssuers**: Both staging (testing) and production issuers using DNS-01 via Route53
- **Certificates**: Gateway TLS certs for `*.k8s.wlkr.ch` and `*.infra.k8s.wlkr.ch`

### cilium/

CNI with Gateway API, WireGuard encryption, and L2 announcements:

- **Gateway API**: Replaces traditional Ingress controller
- **LoadBalancer Pools**: `10.9.2.249` (apps) and `10.9.2.248` (infra)
- **Hubble**: Observability with metrics and UI at `hubble.infra.k8s.wlkr.ch`

### gateway-api/

Kubernetes Gateway API resources:

- **Gateways**: `apps-gateway` for apps, `infra-gateway` for infrastructure
- **HTTPRoutes**: Co-located with each app (not centralized)

### monitoring/

Full observability stack:

- **Prometheus**: Metrics collection with 10-day retention
- **Grafana**: Dashboards at `monitoring.infra.k8s.wlkr.ch`
- **Alertmanager**: Alert routing and notifications

### rook-ceph/

Distributed storage using Ceph:

- **Storage**: Raw partition OSD (`/dev/disk/by-partlabel/rook-osd`)
- **Dashboard**: Ceph UI at `rook.infra.k8s.wlkr.ch`
- **StorageClass**: `rook-ceph-block` (default, RBD-based)

## HTTPRoute Locations

HTTPRoutes are co-located with their respective apps:

| Service | HTTPRoute Location |
|---------|-------------------|
| ArgoCD | `payload/argocd/argocd-httproute.yaml` |
| Grafana | `payload/platform/monitoring/httproute.yaml` |
| Hubble | `payload/platform/cilium/httproute.yaml` |
| Rook Dashboard | `payload/platform/rook-ceph/httproute.yaml` |
| Apps (nginx, etc.) | `payload/workloads/<app>/httproute.yaml` |

## Usage

### Bootstrap (before ArgoCD)

```bash
make install-core  # Installs Cilium + Cert-Manager
make install-argo  # Installs ArgoCD
```

### GitOps (after ArgoCD)

Three parent ArgoCD Applications manage the cluster:

| Application | Path | Description |
|-------------|------|-------------|
| `workloads` | `payload/workloads/` | User applications |
| `platform` | `payload/platform/` | Core platform components |
| `gitops` | `payload/argocd/` | ArgoCD's own config + HTTPRoute |

Excluded from sync:

- `cilium/values.yaml`, `argocd-values.yaml` (Helm values)
- `README.md` (documentation)
- `**/*.template` (credential templates)

Sync wave ordering:

1. `-10`: Gateway API CRDs
2. `-5`: cert-manager
3. `-2`: Rook operator
4. `-1`: Cilium, Rook cluster
5. `1`: Monitoring stack
