---
description: "The core infrastructure components that power the cluster, how traffic flows through them, and the order in which they sync."
---

# Platform

The core infrastructure components that run the cluster. Everything here is
managed by ArgoCD; each component's own page documents its directory layout and
configuration.

## Components

- **[cert-manager](cert-manager.md)**: TLS certificate automation.
- **[cilium](cilium.md)**: CNI, Gateway API, and Network Policies.
- **[external-secrets](external-secrets.md)**: Bridges OpenBao to native K8s Secrets.
- **[gateway-api](gateway-api.md)**: Gateway API resources (Gateways, HTTPRoutes).
- **[monitoring](monitoring.md)**: Observability stack (Prometheus, Grafana).
- **[openbao](openbao.md)**: Cluster-wide secret store.
- **[rook-ceph](rook-ceph.md)**: Distributed storage.

## Traffic Flow

 ```mermaid
 flowchart LR
     Client([Client]) --> LB[Cilium LoadBalancer]
     LB --> GW[Gateway API]
     
     subgraph Cluster
         GW -->|HTTPRoute| Svc[Service]
         Svc --> Pod[App Pod]
     end
 
     style Client fill:#f9f,stroke:#333
 ```

## HTTPRoute Locations

HTTPRoutes are co-located with their respective apps:

<!-- markdownlint-disable MD013 -->
| Service        | URL                             | HTTPRoute Location                           |
|----------------|---------------------------------|----------------------------------------------|
| ArgoCD         | `argo.infra.k8s.wlkr.ch`        | `payload/argocd/httproute.yaml`              |
| Grafana        | `monitoring.infra.k8s.wlkr.ch`  | `payload/platform/monitoring/httproute.yaml` |
| Hubble         | `hubble.infra.k8s.wlkr.ch`      | `payload/platform/cilium/httproute.yaml`     |
| OpenBao UI     | `vault.infra.k8s.wlkr.ch`       | `payload/platform/openbao/httproute.yaml`    |
| Rook Dashboard | `rook.infra.k8s.wlkr.ch`        | `payload/platform/rook-ceph/httproute.yaml`  |
| Apps           | `<app>.k8s.wlkr.ch`             | `payload/workloads/<app>/httproute.yaml`     |
<!-- markdownlint-enable MD013 -->

## Usage

### Bootstrap (before ArgoCD)

```bash
make install-core  # Installs Cilium + Cert-Manager
make install-argo  # Installs ArgoCD
```

### GitOps (after ArgoCD)

Two parent ArgoCD Applications manage the cluster:

<!-- markdownlint-disable MD013 -->
| Application     | Role                            | Path                                |
|-----------------|---------------------------------|-------------------------------------|
| Platform Parent | Core platform components        | `payload/root.yaml` (App: platform) |
| GitOps          | ArgoCD's own config + HTTPRoute | `payload/argocd/`                   |
<!-- markdownlint-enable MD013 -->

A third parent, `workloads`, is added back alongside the first workload. See
[Adding a Workload](../development/add-workload.md).

Excluded from sync:

- `cilium/values.yaml`, `values.yaml` (Helm values)
- `README.md` (documentation)
- `**/*.template` (credential templates)

Sync wave ordering:

1. `-10`: Gateway API CRDs
2. `-5`: cert-manager
3. `-4`: Gateway API
4. `-3`: Rook-Ceph application
5. `-2`: Rook operator
6. `-1`: Cilium, Rook cluster
7. `0`: OpenBao
8. `1`: External Secrets Operator, Monitoring stack
9. `5`: Rook dashboard configuration job
