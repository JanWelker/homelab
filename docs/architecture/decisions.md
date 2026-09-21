---
description: "Why this stack rather than the obvious alternatives, and what each choice costs."
---

# Design Decisions

The rest of this section describes *what* the cluster is. This page covers
*why*, including what each choice gives up. Every one has a reasonable
alternative; none is the only right answer.

## Flatcar Container Linux, not Talos or a general-purpose distro

Flatcar is an immutable, minimal, container-focused OS with A/B updates and
declarative first-boot provisioning through Ignition. Nothing is configured by
hand on a node, so a node is disposable and reproducible from `inventory.yaml`.

Talos goes further — no SSH, no shell, an API-driven machine config — and would
remove a whole class of drift. Flatcar keeps a conventional Linux underneath:
`ssh`, `systemd`, `journalctl` and `kubeadm` work the way the upstream
Kubernetes documentation assumes, which matters more for a cluster that is
also a learning environment than the extra hardening does.

The cost is that Flatcar's read-only `/usr` forces everything unusual into
[sysexts](../concepts.md#systemd-sysexts), Kubernetes and containerd included,
which is the source of the update behaviour in
[Updates & Upgrades](../operations/upgrades.md).

## kubeadm, not k3s or a managed distribution

kubeadm produces a stock upstream cluster: real etcd, standard control-plane
components, and a topology that matches the Kubernetes documentation. k3s would
have been far less work — a single binary, batteries included — at the price of
a bundled, non-standard set of components.

The cost is that everything above the API server is this project's problem:
CNI, ingress, storage and certificates are all installed and sequenced
explicitly. Most of `payload/` exists because of this choice.

## Cilium as CNI, replacing kube-proxy

Cilium replaces `kube-proxy` with eBPF, removing the iptables and IPVS
service-routing path. It also supplies Gateway API, L2 announcements for
LoadBalancer addresses, WireGuard transparent encryption and Hubble for flow
visibility — four things that would otherwise be four components on bare metal.

The cost is a hard bootstrap dependency: with no `kube-proxy`, Cilium needs a
literal API server address in `k8sServiceHost`, which should be the
[control plane VIP](../operations/control-plane-vip.md); pointing it at one
node makes that node a
[single point of failure](limitations.md), and pointing it at an address that
does not answer takes networking down everywhere.

## Gateway API, not Ingress

Ingress is effectively frozen, and its per-controller annotations are why
Ingress manifests are rarely portable. Gateway API separates the cluster-owned
`Gateway` from the app-owned `HTTPRoute`, which fits the split between this
repository and the [workloads repository](../development/add-workload.md)
exactly: the `Gateway` is platform, the `HTTPRoute` ships with the application.

The cost is a smaller ecosystem and more moving parts: CRDs must be installed
before anything references them, which is why they are their own Applications
that every consumer's sync retries against.

## Rook-Ceph, not Longhorn or local volumes

Every node contributes a raw partition, and Ceph turns them into replicated
block storage that survives a node failure. Local `hostPath` volumes would be
simpler and faster, but a node reboot would take its workloads' data with it,
and reboots are routine here because that is
[how updates get applied](../operations/upgrades.md). Longhorn is the closer
alternative and easier to operate; Ceph was chosen for its maturity and because
the same cluster can later serve object and file storage.

The cost: Ceph is the heaviest component in the cluster, wants at least three
nodes, and has its own failure modes and vocabulary. It also only provides
`ReadWriteOnce` here, since CephFS is not deployed.

## OpenBao, not sealed-secrets or SOPS

Sealed-secrets and SOPS keep encrypted material in Git, so rotation is a
commit and revocation is impossible after the fact — the ciphertext is in every
clone, forever. OpenBao keeps secrets out of the repository and hands them to
workloads as ordinary `Secret` objects through the External Secrets Operator.

The cost is a manual unseal after every restart, taken deliberately over an
auto-unseal dependency outside the house — see
[Unsealing after a restart](../platform/openbao.md#unsealing-after-a-restart).

## ArgoCD with an ApplicationSet, not Flux

Either would work. ArgoCD was chosen mainly for its UI, which makes sync state
and drift legible at a glance — worth more in a homelab, where the operator is
often re-learning the system after months away, than Flux's smaller footprint.

Bootstrap is two `kubectl apply`s — the AppProjects and the self-managing
`argocd` Application — and everything else is discovered from the repository
by an ApplicationSet. See [GitOps Strategy](gitops.md).

## CloudNativePG for every database, not the chart's bundled one

Accepting each chart's PostgreSQL subchart is how a cluster ends up running four
Postgres versions from four maintainers, each upgraded on someone else's
schedule. The rule in the [workloads repository](../development/add-workload.md)
is absolute: the subchart is disabled and the application points at a
CloudNativePG `Cluster` in its own namespace. The operator generates the
credentials into a Secret the chart consumes, so no database password is
written down — not in Git, not in OpenBao.

The cost is a controller and a CRD to learn, and a major-version upgrade that
is explicitly this project's problem rather than something arriving silently in
a chart bump — see [CloudNativePG](../platform/cloudnative-pg.md).

## Trivy Operator, not Kubescape

Trivy Operator stores every finding as a plain CRD and scans with the same
Trivy that CI uses, so a number on the dashboard can be checked against an
object with `kubectl`. Kubescape put results behind an aggregated API server on
its own volume, silently reported zero findings for images whose SBOM exceeded
a size ceiling, and needed its scanner image pinned ahead of the chart before
scheduled scans ran. What was lost is relevancy: Kubescape's eBPF agent could
mark a finding as loaded at runtime, and that argument now has to be made from
the workload's configuration.
