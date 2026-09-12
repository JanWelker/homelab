---
description: "Why this stack rather than the obvious alternatives, and what each choice costs."
---

# Design Decisions

The rest of the architecture section describes *what* this cluster is. This page
covers *why*, including what each choice gives up. Every one of these has a
reasonable alternative; none of them is the only right answer.

If you have been doing this long enough, you know that architecture documents
listing only the benefits of each choice are marketing. The costs below are the
useful half — they are the things that will actually wake you up.

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
When something is broken at midnight, being able to `journalctl -u kubelet` is
worth a great deal.

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
sequenced explicitly. Most of `payload/` exists because of this choice. Nobody
picks kubeadm because it is easy; they pick it because when the upstream docs
say "edit the kube-apiserver manifest", there is one to edit.

## Cilium as CNI, replacing kube-proxy

Cilium replaces `kube-proxy` entirely with eBPF, which removes the iptables and
IPVS service-routing path. Anyone who has ever run `iptables-save | wc -l` on a
busy node and watched the number climb past five figures will understand the
appeal without further argument.

It also supplies Gateway API, L2 announcements for LoadBalancer addresses,
WireGuard transparent encryption, and Hubble for flow visibility — four things
that would otherwise be four separate components on bare metal, where there is
no cloud load balancer to lean on.

The cost is a hard bootstrap ordering dependency: with no `kube-proxy`, Cilium
cannot reach the API server through a Service, so it needs a literal address in
`k8sServiceHost`. That address should be the
[control plane VIP](../operations/control-plane-vip.md); pointing it at a single
node is what made that node a
[single point of failure](limitations.md#single-api-server-endpoint). It is also
a wonderfully effective way to take down cluster networking everywhere at once,
should you ever point it at an address that does not answer.

## Gateway API, not Ingress

Ingress is effectively frozen, and its per-controller annotations are the reason
Ingress manifests are rarely portable — every non-trivial Ingress in existence
is really a controller-specific config file wearing a standard resource as a
disguise. Gateway API separates the cluster-owned `Gateway` from the app-owned
`HTTPRoute`, which fits the split between `payload/platform/` and
`payload/workloads/` exactly.

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

The cost is real, and worth stating plainly: Ceph is the heaviest component in
the cluster, wants at least three nodes, and has its own failure modes and
vocabulary. You will learn what a placement group is. You will learn it at an
inconvenient moment. It also only provides `ReadWriteOnce` here, since CephFS is
not deployed.

## OpenBao, not sealed-secrets or SOPS

Sealed-secrets and SOPS both keep encrypted material in Git, which means
rotation is a commit and revocation is impossible after the fact — the ciphertext
is in every clone, forever. OpenBao keeps secrets out of the repository
entirely and hands them to workloads as ordinary Kubernetes `Secret` objects
through the External Secrets Operator, so nothing in Git is sensitive.

The cost is the sealed-at-startup problem: OpenBao is a stateful dependency of
cert-manager, and a sealed OpenBao means no `ExternalSecret` resolves. The seal
is Shamir and unsealing is manual, so every restart needs an operator with the
key shares. Auto-unseal against a cloud KMS would remove that step and make a
service outside the house a hard dependency of the cluster starting up instead;
the manual step is the side of that trade this cluster takes. See
[Unsealing after a restart](../platform/openbao.md#unsealing-after-a-restart)
and [the resulting limitation](limitations.md#openbao-needs-an-operator-to-unseal-it).

## ArgoCD with App-of-Apps, not Flux

Either would work, and anyone claiming otherwise is selling something. ArgoCD
was chosen mainly for its UI, which makes sync state and drift legible at a
glance — worth more in a homelab, where the operator is often re-learning the
system after three months away, than Flux's smaller footprint.

The App-of-Apps pattern keeps bootstrap to a single `kubectl apply` of
`payload/root.yaml`; everything else is discovered from the repository. See
[GitOps Strategy](gitops.md).

## The boot server is a container, in two places

`serve.py` used to be a `sudo` process in this project's virtualenv on the
deployment host. It is now an image: run with `--network host` on the external
boot host, and as a Deployment pinned to a control-plane node in the cluster.
The same bytes either way, which is the point — a node rebuilt from the cluster
cannot behave differently from one rebuilt from the rack.

Host networking in both places is not a shortcut. TFTP answers from a fresh
ephemeral port, which neither a published container port nor a Kubernetes
`Service` reverse-translates, so the node gets a reply from an address it never
spoke to and its firmware discards it in silence. A LoadBalancer IP from the
Cilium pool would be tidier and would not boot a single machine.

The costs are both written into the manifest. The in-cluster server is **pinned
to one node**, because its address is what DHCP and the generated PXE menus
name, so the pod is not free to reschedule — and that node is the one node it
cannot rebuild. And it ships at **zero replicas with `/spec/replicas` ignored by
ArgoCD**, which is a deliberate hole in "everything is reconciled from Git": a
server that hands out kubeadm join credentials to anything that asks is not
something to leave switched on, and not something `selfHeal` should be switching
back off mid-boot either.

The alternative was iPXE with HTTP chainloading, which removes TFTP and with it
all of the above. It also moves the problem into the DHCP server, which is the
one piece of this network the project does not own. See
[In-Cluster Boot Server](../boot_server/in-cluster.md).
