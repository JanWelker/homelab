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

Alphabetical, with the namespace it lands in and the sync wave it lands at.
The [wave ordering](#usage) below explains why those numbers are what they are.

| Component | Namespace | Wave | What it does |
| --- | --- | --- | --- |
| [authentik](authentik.md) | `authentik` | `2` | Single sign-on for every platform UI |
| backup | `backup` | `1` | Velero, the CSI snapshot controller and an etcd snapshot CronJob — see [Backups & Recovery](../operations/backups.md) |
| [cert-manager](cert-manager.md) | `cert-manager` | `-5` | Let's Encrypt wildcards over a Route53 DNS-01 challenge |
| [cilium](cilium.md) | `kube-system` | `-1` | CNI, `kube-proxy` replacement, Gateway API, LoadBalancer addresses, WireGuard, Hubble |
| [external-dns](external-dns.md) | `external-dns` | `2` | Publishes Route53 records from HTTPRoutes |
| [external-secrets](external-secrets.md) | `external-secrets` | `-6` | Bridges OpenBao to native Kubernetes Secrets |
| [gateway-api](gateway-api.md) | `gateway-system` | `-4` | The two Gateways and the HTTP-to-HTTPS redirect |
| kube-vip | `kube-system` | `-1` | Holds the control-plane VIP; adopts the static pod Ignition bootstraps — see [Control Plane VIP](../operations/control-plane-vip.md) |
| kubelet-csr-approver | `kubelet-csr-approver` | `1` | Approves `kubelet-serving` CSRs against the inventory — see [Metrics Server](metrics-server.md#verifying-the-kubelet-instead-of-trusting-it) |
| [kubescape](kubescape.md) | `kubescape` | `2` | Nightly CIS, NSA and MITRE posture scans, exported to Grafana |
| [kured](kured.md) | `kured` | `2` | Drains and reboots nodes to apply staged OS, Kubernetes and containerd updates |
| [logging](logging.md) | `logging` | `1` | Loki and Grafana Alloy, for container, journal and audit logs |
| [metrics-server](metrics-server.md) | `kube-system` | `2` | The `metrics.k8s.io` API behind `kubectl top` and every HPA |
| [monitoring](monitoring.md) | `monitoring` | `1` | Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics |
| [openbao](openbao.md) | `openbao` | `0` | Cluster-wide secret store |
| [rook-ceph](rook-ceph.md) | `rook-ceph` | `-3` to `0` | Replicated block storage and an S3 object store |
| [security policies](security-policies.md) | `kube-system` | `3` | Pod Security Admission levels and default-deny ingress policies |

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

## Usage

### Bootstrap (before ArgoCD)

```bash
make bootstrap  # Gateway API CRDs + Cilium, ArgoCD, then the parent Applications
```

### GitOps (after ArgoCD)

Two parent ArgoCD Applications manage the cluster:

| Application     | Role                            | Path                                |
|-----------------|---------------------------------|-------------------------------------|
| Platform Parent | Core platform components        | `payload/root.yaml` (App: platform) |
| GitOps          | ArgoCD's own config + HTTPRoute | `payload/argocd/`                   |

A third parent, `workloads`, is added back alongside the first workload. See
[Adding a Workload](../development/add-workload.md).

Excluded from sync:

- `cilium/values.yaml`, `values.yaml` (Helm values)
- `README.md` (documentation)
- `**/*.template` (credential templates)

Sync wave ordering. This is the dependency graph made explicit, and it is the
reason a fresh bootstrap converges rather than deadlocking on a CRD that does
not exist yet:

| Wave | Applications | Other resources in the wave |
| --- | --- | --- |
| `-10` | `gateway-api-crds` | |
| `-6` | `external-secrets` | |
| `-5` | `cert-manager` | |
| `-4` | `gateway-api` | |
| `-3` | `rook-ceph` | |
| `-2` | `rook-ceph-operator` | |
| `-1` | `cilium`, `kube-vip`, `rook-ceph-cluster` | |
| `0` | `openbao` | Ceph CSI `OperatorConfig` and `Driver` |
| `1` | `kube-prometheus-stack`, `logging`, `kubelet-csr-approver`, `backup` | the `route53-credentials` ExternalSecret |
| `2` | `authentik`, `external-dns`, `kubescape`, `kured`, `loki`, `metrics-server`, `snapshot-controller` | both Let's Encrypt `ClusterIssuer`s |
| `3` | `alloy`, `velero`, `security` | the gateway `Certificate`s, the `VolumeSnapshotClass` |

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
