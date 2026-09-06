---
description: "The core infrastructure components that power the cluster, how traffic flows through them, and the order in which they sync."
---

# Platform

The core infrastructure components that run the cluster. Everything here is
managed by ArgoCD; each component's own page documents its directory layout and
configuration.

Fourteen components sounds like a lot for a homelab, and it is — but every one of
them exists because bare metal does not come with the thing a cloud provider
would have handed you. No load balancer, no managed certificates, no block
storage API, no identity provider, no backup service. This section is the bill
for not having those.

## Components

- **[authentik](authentik.md)**: Single sign-on for every platform UI.
- **backup**: Velero, the CSI snapshot controller and an etcd snapshot CronJob —
  see [Backups & Recovery](../operations/backups.md).
- **[cert-manager](cert-manager.md)**: TLS certificate automation.
- **[cilium](cilium.md)**: CNI and Gateway API. Enforces the policies in
  [security policies](security-policies.md).
- **[external-dns](external-dns.md)**: Publishes Route53 records from HTTPRoutes.
- **[external-secrets](external-secrets.md)**: Bridges OpenBao to native K8s Secrets.
- **[gateway-api](gateway-api.md)**: Gateway API resources (Gateways, HTTPRoutes).
- **[kured](kured.md)**: Drains and reboots nodes to apply staged OS, Kubernetes
  and containerd updates.
- **[logging](logging.md)**: Loki and Grafana Alloy, for container and node logs.
- **[metrics-server](metrics-server.md)**: The `metrics.k8s.io` resource metrics
  API, behind `kubectl top` and every HPA. Deployed with kubelet-csr-approver so
  kubelet certificates are verified rather than skipped.
- **[monitoring](monitoring.md)**: Observability stack (Prometheus, Grafana).
- **[openbao](openbao.md)**: Cluster-wide secret store.
- **[rook-ceph](rook-ceph.md)**: Distributed storage.
- **[security policies](security-policies.md)**: Pod Security Admission levels and
  default-deny ingress policies.

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

Four hops, and Cilium is three of them. When a hostname stops answering, the
question is which hop stopped: does the Gateway still hold its LoadBalancer IP,
does the `HTTPRoute` still say `Accepted`, does the Service still have endpoints.
In that order — the answer is usually the first one.

## HTTPRoute Locations

HTTPRoutes are co-located with their respective apps:

<!-- markdownlint-disable MD013 -->
| Service        | URL                              | HTTPRoute Location                           |
|----------------|----------------------------------|----------------------------------------------|
| ArgoCD         | `argo.infra.k8s.wlkr.ch`         | `payload/argocd/httproute.yaml`              |
| Authentik      | `auth.infra.k8s.wlkr.ch`         | `payload/platform/authentik/httproute.yaml`  |
| Prometheus     | `prometheus.infra.k8s.wlkr.ch`   | `payload/platform/authentik/httproute.yaml`  |
| Alertmanager   | `alertmanager.infra.k8s.wlkr.ch` | `payload/platform/authentik/httproute.yaml`  |
| Grafana        | `monitoring.infra.k8s.wlkr.ch`   | `payload/platform/monitoring/httproute.yaml` |
| Hubble         | `hubble.infra.k8s.wlkr.ch`       | `payload/platform/cilium/httproute.yaml`     |
| OpenBao UI     | `vault.infra.k8s.wlkr.ch`        | `payload/platform/openbao/httproute.yaml`    |
| Rook Dashboard | `rook.infra.k8s.wlkr.ch`         | `payload/platform/rook-ceph/httproute.yaml`  |
| Apps           | `<app>.k8s.wlkr.ch`              | `payload/workloads/<app>/httproute.yaml`     |
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

Sync wave ordering. This is the dependency graph made explicit, and it is the
reason a fresh bootstrap converges rather than deadlocking on a CRD that does
not exist yet:

1. `-10`: Gateway API CRDs
2. `-5`: cert-manager
3. `-4`: Gateway API
4. `-3`: Rook-Ceph application
5. `-2`: Rook operator
6. `-1`: Cilium, Rook cluster
7. `0`: OpenBao
8. `1`: External Secrets Operator, Monitoring stack, kubelet-csr-approver,
   logging, backup
9. `2`: Authentik, external-dns, Kured, Loki, metrics-server,
   snapshot-controller
10. `3`: Alloy, Velero, Pod Security Admission labels and network policies
11. `5`: Rook dashboard configuration job

The negative waves are the interesting half: nothing above wave `0` can work
until networking, storage and certificates exist, so those get to go first and
everything else waits its turn.
