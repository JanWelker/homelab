# Platform

The core infrastructure components that run the cluster, all managed by ArgoCD. Every one of them exists because bare metal does not come with the thing a cloud provider would have handed you: no load balancer, no managed certificates, no block storage API, no identity provider, no backup service.

## Components

One row per directory under `payload/platform/`, with the Application name where it differs from the directory — that is the name `kubectl -n argocd get applications` shows — and the namespace it lands in.

| Component | Namespace | What it does |
| --- | --- | --- |
| alloy | `logging` | Grafana Alloy, the log shipper — see [Logging](<https://homelab.wlkr.ch/platform/logging/index.md>) |
| arc-controller | `arc-systems` | The Actions Runner Controller — see [GitHub runners](<https://homelab.wlkr.ch/platform/github-runners/index.md>) |
| [arc-runners](<https://homelab.wlkr.ch/platform/github-runners/index.md>) | `arc-runners`, `arc-jobs` | Self-hosted GitHub Actions runners, one scale set per repository |
| [argocd](<https://homelab.wlkr.ch/platform/argocd/index.md>) | `argocd` | ArgoCD itself and the `platform` ApplicationSet |
| argocd-config | `argocd` | The `ExternalSecret`s for ArgoCD's OIDC client and the previews' GitHub token, and its Grafana dashboard; see [ArgoCD](<https://homelab.wlkr.ch/platform/argocd/index.md>) |
| argocd-projects | `argocd` | The AppProjects; see [GitOps Strategy](<https://homelab.wlkr.ch/architecture/gitops/#structure>) |
| [authentik](<https://homelab.wlkr.ch/platform/authentik/index.md>) | `authentik` | Single sign-on for every platform UI |
| backup | `backup` | Velero's buckets, the `VolumeSnapshotClass` and an etcd snapshot CronJob — see [Backups &amp; Recovery](<https://homelab.wlkr.ch/operations/backups/index.md>) |
| [cert-manager](<https://homelab.wlkr.ch/platform/cert-manager/index.md>) | `cert-manager` | The cert-manager chart |
| certificates | `cert-manager` | The Let's Encrypt issuers and the Gateway wildcard certificates — see [cert-manager](<https://homelab.wlkr.ch/platform/cert-manager/index.md>) |
| [cilium](<https://homelab.wlkr.ch/platform/cilium/index.md>) | `kube-system` | CNI, `kube-proxy` replacement, Gateway API, LoadBalancer addresses, WireGuard, Hubble |
| [cloudnative-pg](<https://homelab.wlkr.ch/platform/cloudnative-pg/index.md>) | `cnpg-system` | The PostgreSQL operator every workload database runs on |
| [descheduler](<https://homelab.wlkr.ch/platform/descheduler/index.md>) | `descheduler` | Evicts pods a drain or reboot left piled on the surviving nodes |
| [external-dns](<https://homelab.wlkr.ch/platform/external-dns/index.md>) | `external-dns` | Publishes Route53 records from HTTPRoutes |
| [external-secrets](<https://homelab.wlkr.ch/platform/external-secrets/index.md>) | `external-secrets` | Bridges OpenBao to native Kubernetes Secrets |
| [gateway-api](<https://homelab.wlkr.ch/platform/gateway-api/index.md>) | `kube-system` | The two Gateways and the HTTP-to-HTTPS redirect |
| gateway-api-crds | `kube-system` | The Gateway API CRDs — see [Gateway API](<https://homelab.wlkr.ch/platform/gateway-api/index.md>) |
| kube-vip | `kube-system` | Holds the control-plane VIP; adopts the static pod Ignition bootstraps — see [Control Plane VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>) |
| kubelet-csr-approver | `kubelet-csr-approver` | Approves `kubelet-serving` CSRs against the inventory — see [Metrics Server](<https://homelab.wlkr.ch/platform/metrics-server/#verifying-the-kubelet-instead-of-trusting-it>) |
| [kured](<https://homelab.wlkr.ch/platform/kured/index.md>) | `kured` | Drains and reboots nodes to apply staged OS and Kubernetes updates |
| [logging](<https://homelab.wlkr.ch/platform/logging/index.md>) | `logging` | Loki's bucket, rules, dashboards and Grafana datasource |
| loki | `logging` | The Loki chart — see [Logging](<https://homelab.wlkr.ch/platform/logging/index.md>) |
| [metrics-server](<https://homelab.wlkr.ch/platform/metrics-server/index.md>) | `kube-system` | The `metrics.k8s.io` API behind `kubectl top` and every HPA |
| monitoring, Application `kube-prometheus-stack` | `monitoring`, `node-exporter` | Prometheus, Grafana, Alertmanager, kube-state-metrics; node-exporter alone in `node-exporter` — see [Monitoring](<https://homelab.wlkr.ch/platform/monitoring/index.md>) |
| [openbao](<https://homelab.wlkr.ch/platform/openbao/index.md>) | `openbao` | Cluster-wide secret store |
| prometheus-operator-crds | `monitoring` | The Prometheus operator CRDs, separate so every chart that ships a `ServiceMonitor` can sync — see [Monitoring](<https://homelab.wlkr.ch/platform/monitoring/#crds>) |
| [rook-ceph](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) | `rook-ceph` | The CSI driver, dashboards and HTTPRoute |
| rook-ceph-cluster | `rook-ceph` | The `CephCluster`, pools and storage classes — see [Rook-Ceph](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) |
| rook-ceph-operator | `rook-ceph` | The Rook operator chart — see [Rook-Ceph](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) |
| security | `kube-system` | Pod Security Admission levels, the network policies and image admission policies — see [Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md>) |
| snapshot-controller | `backup` | The CSI snapshot controller Velero's volume snapshots need — see [Backups &amp; Recovery](<https://homelab.wlkr.ch/operations/backups/index.md>) |
| [tetragon](<https://homelab.wlkr.ch/platform/tetragon/index.md>) | `kube-system` | Runtime detection: execs, credential changes and sensitive file reads from the kernel |
| [trivy-operator](<https://homelab.wlkr.ch/platform/trivy-operator/index.md>) | `trivy-system` | Vulnerability, misconfiguration, secret, RBAC and CIS scanning, every finding a CRD |
| [velero](<https://homelab.wlkr.ch/platform/velero/index.md>) | `backup` | Volume and resource backups to the Ceph object store |

Across the platform, memory limits are set at roughly 2.5x the measured peak working set and requests at steady state; CPU is requested but never limited. The control-plane static pods get their memory requests through the kubeadm patches in `ansible/templates/butane_node_config.yaml.j2`, applied at `kubeadm init`, `join` and `upgrade`, so a running node picks them up at its next reinstall; they carry no limits, because a limit on the API server or etcd would OOM-kill the control plane and 2.5x their peak exceeds the node. CoreDNS is kubeadm's too: the same directory holds a patch spreading its replicas one per node, which kubeadm applies at `init` and `upgrade apply` only, and its PodDisruptionBudget sits in the `security` component because nothing else owns a CoreDNS manifest.

Components the cluster cannot run without — the CNI and API VIP, the admission webhooks, the secret and certificate chain, the CSR approver, the storage daemons and the node security agents — carry a `system-*` priority class, so under node pressure the kubelet evicts workloads first: `system-node-critical` for the DaemonSets a node needs, `system-cluster-critical` for the rest.

## Traffic Flow

```
flowchart LR
    Client([Client]) --> LB[Cilium LoadBalancer]
    LB --> GW[Gateway API]

    subgraph Cluster
        GW -->|HTTPRoute| Svc[Service]
        Svc --> Pod[App Pod]
    end

    style Client fill:#f9f,stroke:#333
```

Four hops, and Cilium is three of them. When a hostname stops answering, ask which hop stopped, in this order: does the Gateway still hold its LoadBalancer IP, does the `HTTPRoute` still say `Accepted`, does the Service still have endpoints.

## HTTPRoute Locations

Routes sit with the component they expose, as a manifest where the component is plain YAML and as chart values where the chart renders its own:

| Service | URL | Route |
| --- | --- | --- |
| ArgoCD | `argo.infra.k8s.wlkr.ch` | `server.httproute` in `payload/argocd/values.yaml` |
| ArgoCD CLI (gRPC) | `argo-grpc.infra.k8s.wlkr.ch` | `server.grpcroute` in `payload/argocd/values.yaml` |
| Authentik | `auth.k8s.wlkr.ch` | `payload/platform/authentik/httproute.yaml` |
| Prometheus | `prometheus.infra.k8s.wlkr.ch` | `payload/platform/monitoring/httproute.yaml` |
| Alertmanager | `alertmanager.infra.k8s.wlkr.ch` | `payload/platform/monitoring/httproute.yaml` |
| Grafana | `monitoring.infra.k8s.wlkr.ch` | `grafana.route.main` in `payload/platform/monitoring/application.yaml` |
| Hubble | `hubble.infra.k8s.wlkr.ch` | `payload/platform/cilium/httproute.yaml` |
| OpenBao UI | `vault.infra.k8s.wlkr.ch` | `server.gateway.httpRoute` in `payload/platform/openbao/application.yaml` |
| Rook dashboard | `rook.infra.k8s.wlkr.ch` | `payload/platform/rook-ceph/httproute.yaml` |
| Ceph object store | `s3.infra.k8s.wlkr.ch` | `payload/platform/rook-ceph/httproute.yaml` |
| Home Assistant | `home.k8s.wlkr.ch` | `home-assistant/httproute.yaml` in [homelab-apps](<https://github.com/JanWelker/homelab-apps>) |

## Deployment

```bash
make bootstrap  # Gateway API CRDs + Cilium, ArgoCD, then the handover
```

After that the `argocd` Application syncs the `platform` ApplicationSet, which generates one Application per `payload/platform/*/application.yaml` plus the `workloads` Application that deploys the `apps` ApplicationSet for the [workloads repository](<https://homelab.wlkr.ch/development/add-workload/index.md>). Each component directory holds exactly one `application.yaml`; everything else in it is what that Application deploys. [GitOps Strategy](<https://homelab.wlkr.ch/architecture/gitops/index.md>) has the structure and the sync policy.

## Replica placement

Two replicas on one node are not redundancy. Every chart's default is a single `preferredDuringSchedulingIgnoredDuringExecution` anti-affinity term — one weight among the scheduler's scores, and on a cluster of one 16 GB worker against two 8 GB ones, free memory outscores it. So every component with more than one replica carries an explicit rule:

| Rule | When |
| --- | --- |
| `topologySpreadConstraints`, `maxSkew: 1`, `whenUnsatisfiable: ScheduleAnyway` | the component runs on the three workers only, where a hard rule would leave a replica `Pending` through a Kured drain |
| `podAntiAffinity` `requiredDuringSchedulingIgnoredDuringExecution` | the component tolerates the control planes, so every node is a candidate and a drain always leaves room — or its chart exposes no spread constraint |

Constraints carry `nodeTaintsPolicy: Honor`, so the skew is measured over the nodes the pod can reach rather than over the tainted control planes. Where a component carries a PodDisruptionBudget it says `maxUnavailable: 1`, so a drain or the [descheduler](<https://homelab.wlkr.ch/platform/descheduler/index.md>) evicts one replica at a time; `maxUnavailable` rather than `minAvailable`, so a component scaled down to one replica never blocks a drain. The CloudNativePG operator runs two replicas with no budget. Placement is scored once, at scheduling time: pods already sharing a node stay there until something restarts them, and the [descheduler](<https://homelab.wlkr.ch/platform/descheduler/index.md>) moves them back only if its evictor is allowed to touch them.

## Rollout order

Nothing enforces an order: every Application syncs as soon as it exists and converges by retry and health, as [GitOps Strategy](<https://homelab.wlkr.ch/architecture/gitops/#bootstrap-convergence>) explains. This is the order a fresh cluster settles in, and what each layer is waiting for while it is not yet Healthy.

| Layer | Applications | Waits for |
| --- | --- | --- |
| Projects and CRDs | `argocd-projects`, `gateway-api-crds`, `prometheus-operator-crds` | — |
| Network | `cilium`, `kube-vip` | the Gateway API CRDs Cilium's operator reads at startup |
| Controllers | `arc-controller`, `cert-manager`, `cloudnative-pg`, `external-secrets`, `kubelet-csr-approver`, `rook-ceph-operator`, `snapshot-controller` | a network; each brings its own CRDs |
| Storage | `rook-ceph`, `rook-ceph-cluster` | the Rook operator and its CRDs |
| Secrets | `openbao` | `rook-ceph-block` for its volumes, then an operator — see [Bootstrap pauses at OpenBao](<https://homelab.wlkr.ch/architecture/gitops/#bootstrap-pauses-at-openbao>) |
| Certificates | `certificates`, `external-dns` | a working `ClusterSecretStore` and the Route53 credentials in OpenBao |
| Ingress | `gateway-api` | the wildcard certificates the Gateways terminate TLS with |
| Services | `arc-runners`, `argocd-config`, `authentik`, `backup`, `kube-prometheus-stack`, `logging`, `metrics-server` | secrets, storage, the Gateways, and approved kubelet certificates |
| Backends | `loki`, `velero` | the buckets `logging` and `backup` claim |
| Agents | `alloy`, `tetragon` | Loki, so the collector has somewhere to ship |
| Policy | `descheduler`, `kured`, `security`, `trivy-operator` | namespaces to label, a Prometheus to scrape and metrics-server for node usage; kured reboots only on a staged update, never on a fresh install |
| Workloads | `workloads` | nothing on its own; the workloads it generates wait for the platform resources they reference — see the [workloads repository](<https://homelab.wlkr.ch/development/add-workload/index.md>) |

Why the `ClusterSecretStore` sits with OpenBao and the issuers are their own Application is in [Bootstrap convergence](<https://homelab.wlkr.ch/architecture/gitops/#bootstrap-convergence>).
