---
description: "Why this stack rather than the obvious alternatives, and what each choice costs."
---

# Design Decisions

The rest of the architecture section describes *what* this cluster is. This page
covers *why*, including what each choice gives up. Every one of these has a
reasonable alternative; none of them is the only right answer.

## Flatcar Container Linux, not Talos or a general-purpose distro

Flatcar gives an immutable, minimal, container-focused OS with A/B updates and
declarative first-boot provisioning through Ignition. Nothing is configured by
hand on a node, which means a node is disposable and reproducible from
`inventory.yaml`.

Talos goes further — no SSH, no shell, an API-driven machine config — and would
remove a whole class of drift. Flatcar was chosen instead because it keeps a
conventional Linux underneath: `ssh`, `systemd`, `journalctl` and `kubeadm` all
work the way the upstream Kubernetes documentation assumes, which matters more
for a cluster that is also a learning environment than the extra hardening does.

The cost is that Flatcar's read-only `/usr` forces everything unusual into
[sysexts](index.md#systemd-sysexts) — including Kubernetes and containerd
themselves — which is the source of the update behaviour described in
[Updates & Upgrades](../operations/upgrades.md).

## kubeadm, not k3s or a managed distribution

kubeadm produces a stock upstream cluster: real etcd, standard control-plane
components, and a topology that matches what the Kubernetes documentation
describes. k3s would have been dramatically less work — a single binary, batteries
included — at the price of a bundled, non-standard set of components.

The cost of kubeadm is that everything above the API server is now this
project's problem: CNI, ingress, storage and certificates are all installed and
sequenced explicitly. Most of `payload/` exists because of this choice.

## Cilium as CNI, replacing kube-proxy

Cilium replaces `kube-proxy` entirely with eBPF, which removes the iptables and
IPVS service-routing path. It also supplies Gateway API, L2 announcements for
LoadBalancer addresses, WireGuard transparent encryption, and Hubble for flow
visibility — four things that would otherwise be four separate components on
bare metal, where there is no cloud load balancer to lean on.

The cost is a hard bootstrap ordering dependency: with no `kube-proxy`, Cilium
cannot reach the API server through a Service, so it needs a literal address in
`k8sServiceHost`. That address should be the
[control plane VIP](../operations/control-plane-vip.md); pointing it at a single
node is what made that node a
[single point of failure](limitations.md#single-api-server-endpoint).

## Gateway API, not Ingress

Ingress is effectively frozen, and its per-controller annotations are the reason
Ingress manifests are rarely portable. Gateway API separates the cluster-owned
`Gateway` from the app-owned `HTTPRoute`, which fits the split between
`payload/platform/` and `payload/workloads/` exactly.

The cost is a smaller ecosystem and more moving parts: CRDs must be installed
before anything that references them, which is why they occupy sync wave `-10`.

## Rook-Ceph, not Longhorn or local volumes

Every node contributes a raw partition, and Ceph turns them into replicated
block storage that survives a node failure. Local `hostPath` volumes would be
simpler and much faster, but any node reboot would take its workloads' data with
it — and node reboots are routine here, because that is
[how updates get applied](../operations/upgrades.md).

Longhorn is the closer alternative and is easier to operate. Ceph was chosen for
its maturity and because the same cluster can later serve object and file
storage, not just block.

The cost is real: Ceph is the heaviest component in the cluster, wants at least
three nodes, and has its own failure modes and vocabulary. It also only provides
`ReadWriteOnce` here, since CephFS is not deployed.

## OpenBao, not sealed-secrets or SOPS

Sealed-secrets and SOPS both keep encrypted material in Git, which means
rotation is a commit and revocation is impossible after the fact. OpenBao keeps
secrets out of the repository entirely and hands them to workloads as ordinary
Kubernetes `Secret` objects through the External Secrets Operator, so nothing in
Git is sensitive.

The cost is the sealed-at-startup problem: OpenBao is a stateful dependency that
requires manual intervention after every restart, and it is a hard dependency of
cert-manager. See
[the unsealing limitation](limitations.md#openbao-must-be-unsealed-by-hand).

## ArgoCD with App-of-Apps, not Flux

Either would work. ArgoCD was chosen mainly for its UI, which makes sync state
and drift legible at a glance — worth more in a homelab, where the operator is
often re-learning the system, than Flux's smaller footprint.

The App-of-Apps pattern keeps bootstrap to a single `kubectl apply` of
`payload/root.yaml`; everything else is discovered from the repository. See
[GitOps Strategy](gitops.md).
