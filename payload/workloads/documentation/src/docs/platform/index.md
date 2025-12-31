# Core Infrastructure

This directory contains **core infrastructure components** managed via ArgoCD GitOps.

## Directory Structure

See individual component documentation for detailed structure.

## Components

- **[cert-manager](cert-manager.md)**: TLS certificate automation.
- **[cilium](cilium.md)**: CNI, Gateway API, and Network Policies.
- **[gateway-api](gateway-api.md)**: Gateway API resources (Gateways, HTTPRoutes).
- **[infisical](infisical.md)**: Secrets management.
- **[k8up](k8up.md)**: Backup operator.
- **[monitoring](monitoring.md)**: Observability stack (Prometheus, Grafana).
- **[postgres-operator](postgres-operator.md)**: Postgres operator.
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

| Service            | HTTPRoute Location                           |
|--------------------|----------------------------------------------|
| ArgoCD             | `payload/argocd/httproute.yaml`              |
| Grafana            | `payload/platform/monitoring/httproute.yaml` |
| Hubble             | `payload/platform/cilium/httproute.yaml`     |
| Rook Dashboard     | `payload/platform/rook-ceph/httproute.yaml`  |
| Apps (nginx, etc.) | `payload/workloads/<app>/httproute.yaml`     |

## Usage

### Bootstrap (before ArgoCD)

```bash
make install-core  # Installs Cilium + Cert-Manager
make install-argo  # Installs ArgoCD
```

### GitOps (after ArgoCD)

Three parent ArgoCD Applications manage the cluster:

<!-- markdownlint-disable MD013 -->
| Application      | Role                            | Path                                    |
|------------------|---------------------------------|-----------------------------------------|
| Platform Parent  | Core platform components        | `payload/root.yaml` (App: platform)     |
| Workloads Parent | User applications               | `payload/root.yaml` (App: workloads)    |
| GitOps           | ArgoCD's own config + HTTPRoute | `payload/argocd/`                       |
<!-- markdownlint-enable MD013 -->

Excluded from sync:

- `cilium/values.yaml`, `values.yaml` (Helm values)
- `README.md` (documentation)
- `**/*.template` (credential templates)

Sync wave ordering:

1. `-10`: Gateway API CRDs
2. `-5`: cert-manager
3. `-2`: Rook operator
4. `-1`: Cilium, Rook cluster
5. `1`: Monitoring stack
