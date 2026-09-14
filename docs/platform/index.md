---
description: "The core infrastructure components that power the cluster, how traffic flows through them, and the order in which they sync."
---

# Platform

The core infrastructure components that run the cluster. Everything here is
managed by ArgoCD; each component's own page documents its directory layout and
configuration.

That is a lot of components for a homelab, and every one of them exists because
bare metal does not come with the thing a cloud provider would have handed you. No load balancer, no managed certificates, no block
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
- **kubelet-csr-approver**: Approves `kubelet-serving` certificate requests
  against the inventory, so kubelet TLS is verified rather than skipped — see
  [Metrics Server](metrics-server.md#verifying-the-kubelet-instead-of-trusting-it).
- **[kubescape](kubescape.md)**: Scans the cluster against CIS, NSA and MITRE
  nightly and exports the findings to Grafana.
- **[kured](kured.md)**: Drains and reboots nodes to apply staged OS, Kubernetes
  and containerd updates.
- **[logging](logging.md)**: Loki and Grafana Alloy, for container and node logs.
- **[metrics-server](metrics-server.md)**: The `metrics.k8s.io` resource metrics
  API, behind `kubectl top` and every HPA.
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
make install-core  # Gateway API + Prometheus operator CRDs, Cilium, cert-manager
make install-argo  # ArgoCD
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

<!-- markdownlint-disable MD013 -->
| Wave | Applications | Other resources in the wave |
| --- | --- | --- |
| `-10` | `gateway-api-crds` | |
| `-6` | `external-secrets` | |
| `-5` | `cert-manager` | |
| `-4` | `gateway-api` | |
| `-3` | `rook-ceph` | |
| `-2` | `rook-ceph-operator` | |
| `-1` | `cilium`, `rook-ceph-cluster` | |
| `0` | `openbao` | Ceph CSI `OperatorConfig` and `Driver` |
| `1` | `kube-prometheus-stack`, `logging`, `kubelet-csr-approver`, `backup` | the `route53-credentials` ExternalSecret |
| `2` | `authentik`, `external-dns`, `kubescape`, `kured`, `loki`, `metrics-server`, `snapshot-controller` | both Let's Encrypt `ClusterIssuer`s |
| `3` | `alloy`, `velero`, `security` | the gateway `Certificate`s, the `VolumeSnapshotClass` |
<!-- markdownlint-enable MD013 -->

Waves `1` through `3` are where a component and its own children separate:
`logging` is the parent Application at `1`, and it brings Loki at `2` and Alloy
at `3`, in that order because a collector with nowhere to ship is just a
collector. The same split puts `backup` at `1` ahead of the snapshot controller
it needs at `2` and the Velero that needs both at `3`.

The negative waves are the interesting half: nothing above wave `0` can work
until networking, storage and certificates exist, so those get to go first and
everything else waits its turn.

External Secrets sits at the very front despite needing OpenBao, which arrives
six waves later, because what the wave has to guarantee is the *CRD*, not a
working secret store. An `ExternalSecret` whose CRD is missing is not applied
at all, and ArgoCD keeps the operation open waiting for the rest of the wave —
so cert-manager's Route53 credential, which sits beside the `Certificate`
resources that cannot go Ready without it, would wait for a retry that never
comes. A store that is not ready yet is a normal, self-correcting state; a
resource that was never applied is not.
