---
description: "The core infrastructure components that power the cluster, how traffic flows through them, and the order in which they roll out."
---

# Platform

The core infrastructure components that run the cluster, all managed by
ArgoCD. Every one of them exists because bare metal does not come with the
thing a cloud provider would have handed you: no load balancer, no managed
certificates, no block storage API, no identity provider, no backup service.

## Components

Alphabetical, with the namespace it lands in and the [rollout stage](#rollout-order)
it belongs to.

| Component | Namespace | Stage | What it does |
| --- | --- | --- | --- |
| [argocd](argocd.md) | `argocd` | hand-applied | ArgoCD itself and the `platform` ApplicationSet |
| argocd-config | `argocd` | `08-services` | ArgoCD's own HTTPRoute, OIDC credentials and Grafana dashboard — see [ArgoCD](argocd.md) |
| argocd-projects | `argocd` | `00-projects` | The `apps`, `infra` and `system` AppProjects |
| [authentik](authentik.md) | `authentik` | `08-services` | Single sign-on for every platform UI |
| backup | `backup` | `08-services` | The CSI snapshot controller, Velero's buckets and an etcd snapshot CronJob — see [Backups & Recovery](../operations/backups.md) |
| [cert-manager](cert-manager.md) | `cert-manager` | `03-controllers`, issuers and certificates `06-certificates` | Let's Encrypt wildcards over a Route53 DNS-01 challenge |
| [cilium](cilium.md) | `kube-system` | `02-network` | CNI, `kube-proxy` replacement, Gateway API, LoadBalancer addresses, WireGuard, Hubble |
| [cloudnative-pg](cloudnative-pg.md) | `cnpg-system` | `03-controllers` | The PostgreSQL operator every workload database runs on |
| [external-dns](external-dns.md) | `external-dns` | `06-certificates` | Publishes Route53 records from HTTPRoutes |
| [external-secrets](external-secrets.md) | `external-secrets` | `03-controllers` | Bridges OpenBao to native Kubernetes Secrets |
| [gateway-api](gateway-api.md) | `gateway-system` | `01-crds` CRDs, `07-ingress` Gateways | The two Gateways and the HTTP-to-HTTPS redirect |
| kube-vip | `kube-system` | `02-network` | Holds the control-plane VIP; adopts the static pod Ignition bootstraps — see [Control Plane VIP](../operations/control-plane-vip.md) |
| kubelet-csr-approver | `kubelet-csr-approver` | `03-controllers` | Approves `kubelet-serving` CSRs against the inventory — see [Metrics Server](metrics-server.md#verifying-the-kubelet-instead-of-trusting-it) |
| [kured](kured.md) | `kured` | `11-policy` | Drains and reboots nodes to apply staged OS, Kubernetes and containerd updates |
| [logging](logging.md) | `logging` | `08-services` to `10-agents` | Loki and Grafana Alloy, for container, journal and audit logs |
| [metrics-server](metrics-server.md) | `kube-system` | `08-services` | The `metrics.k8s.io` API behind `kubectl top` and every HPA |
| [monitoring](monitoring.md) | `monitoring` | `01-crds` CRDs, `08-services` stack | Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics |
| [openbao](openbao.md) | `openbao` | `05-secrets` | Cluster-wide secret store |
| [rook-ceph](rook-ceph.md) | `rook-ceph` | `03-controllers` operator, `04-storage` cluster | Replicated block storage and an S3 object store |
| [security policies](security-policies.md) | `kube-system` | `11-policy` | Pod Security Admission levels and default-deny ingress policies |
| [velero](velero.md) | `backup` | `09-backends` | Volume and resource backups to the Ceph object store |

Across the platform, memory limits are set at roughly 2.5x the measured peak
working set and requests at steady state; CPU is requested but never limited.

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

Four hops, and Cilium is three of them. When a hostname stops answering, ask
which hop stopped, in this order: does the Gateway still hold its LoadBalancer
IP, does the `HTTPRoute` still say `Accepted`, does the Service still have
endpoints.

## HTTPRoute Locations

HTTPRoutes are co-located with their respective apps:

| Service          | URL                              | HTTPRoute Location                                                                           |
|------------------|----------------------------------|----------------------------------------------------------------------------------------------|
| ArgoCD           | `argo.infra.k8s.wlkr.ch`         | `payload/platform/argocd-config/httproute.yaml`                                              |
| Authentik (apps) | `auth.k8s.wlkr.ch`               | `payload/platform/authentik/httproute.yaml`                                                  |
| Authentik        | `auth.infra.k8s.wlkr.ch`         | `payload/platform/authentik/httproute.yaml`                                                  |
| Prometheus       | `prometheus.infra.k8s.wlkr.ch`   | `payload/platform/authentik/httproute.yaml`                                                  |
| Alertmanager     | `alertmanager.infra.k8s.wlkr.ch` | `payload/platform/authentik/httproute.yaml`                                                  |
| Grafana          | `monitoring.infra.k8s.wlkr.ch`   | `payload/platform/monitoring/httproute.yaml`                                                 |
| Hubble           | `hubble.infra.k8s.wlkr.ch`       | `payload/platform/cilium/httproute.yaml`                                                     |
| OpenBao UI       | `vault.infra.k8s.wlkr.ch`        | `payload/platform/openbao/httproute.yaml`                                                    |
| Rook Dashboard   | `rook.infra.k8s.wlkr.ch`         | `payload/platform/rook-ceph/httproute.yaml`                                                  |
| Home Assistant   | `home.k8s.wlkr.ch`               | `home-assistant/httproute.yaml` in [homelab-apps](https://github.com/JanWelker/homelab-apps) |
| Nextcloud        | `cloud.k8s.wlkr.ch`              | `nextcloud/httproute.yaml` in [homelab-apps](https://github.com/JanWelker/homelab-apps)      |

## Deployment

```bash
make bootstrap  # Gateway API CRDs + Cilium, ArgoCD, then the handover
```

After that the `argocd` Application syncs the `platform` ApplicationSet, which
generates one Application per `payload/platform/*/application.yaml` plus the
`workloads` Application that deploys the `apps` ApplicationSet for the
[workloads repository](../development/add-workload.md). Each component
directory holds exactly one `application.yaml`; everything else in it is what
that Application deploys. [GitOps Strategy](../architecture/gitops.md) has the
structure and what the staging costs.

## Rollout order

The ApplicationSet syncs its Applications in stages, selected by the
`homelab.wlkr.ch/stage` label, and starts a stage only when every Application
in the one before it is Synced and Healthy; [GitOps
Strategy](../architecture/gitops.md#rollout-order) covers how the gating works.

| Stage | Applications | Waits for |
| --- | --- | --- |
| `00-projects` | `argocd-projects` | — |
| `01-crds` | `gateway-api-crds`, `prometheus-operator-crds` | the AppProjects every Application names |
| `02-network` | `cilium`, `kube-vip` | the Gateway API CRDs Cilium's operator reads at startup |
| `03-controllers` | `cert-manager`, `cloudnative-pg`, `external-secrets`, `kubelet-csr-approver`, `rook-ceph-operator`, `snapshot-controller` | a network; each brings its own CRDs |
| `04-storage` | `rook-ceph`, `rook-ceph-cluster` | the Rook operator and its CRDs |
| `05-secrets` | `openbao` | `rook-ceph-block` for its volumes. **Bootstrap pauses here** until OpenBao is initialised and unsealed |
| `06-certificates` | `certificates`, `external-dns` | a working `ClusterSecretStore` and the Route53 credentials in OpenBao |
| `07-ingress` | `gateway-api` | the wildcard certificates the Gateways terminate TLS with |
| `08-services` | `argocd-config`, `authentik`, `backup`, `kube-prometheus-stack`, `logging`, `metrics-server` | secrets, storage, the Gateways, and approved kubelet certificates |
| `09-backends` | `loki`, `velero` | the buckets `logging` and `backup` claim |
| `10-agents` | `alloy` | Loki, so the collector has somewhere to ship |
| `11-policy` | `kured`, `security` | everything else, so policies label namespaces that exist and kured reboots a converged cluster |
| `12-workloads` | `workloads` | the whole platform. It deploys the `apps` ApplicationSet, and nothing in the [workloads repository](../development/add-workload.md) is generated before it |

The `ClusterSecretStore` lives with OpenBao in `05-secrets` rather than with
External Secrets, and cert-manager's issuers and certificates are their own
`certificates` Application, for the same reason: neither can go Ready until
OpenBao holds what they read.
