---
description: "GitOps-driven Kubernetes homelab on bare metal, running Flatcar Container Linux, Kubeadm, Cilium, Rook-Ceph and ArgoCD."
---

# Flatcar Homelab

![Homelab Logo](assets/images/logo.png){ align=right width=150 }

Six machines in a rack, a pile of YAML, and a stubborn refusal to pay a cloud
provider for something that fits under a desk. This is a bare metal Kubernetes
cluster that provisions itself over PXE, keeps its entire state in Git, and is
documented here mostly so that Future Me, at 02:00, with one hand holding a
phone flashlight, does not have to re-derive any of it.

It is a real cluster doing real work, built the way a production cluster is
built, at a scale where breaking it is a learning experience rather than an
incident review.

## Start here

<div class="grid cards" markdown>

- **I want to build this**

    ---

    [Adapt It to Your Cluster](adapting.md) first — the repository URL, domain
    and addresses are hardcoded — then the [Quickstart](quickstart.md). Set
    aside an afternoon.

- **I know Kubernetes, not this repo**

    ---

    [Architecture](architecture/index.md) for how it fits together,
    [Design Decisions](architecture/decisions.md) for why, and
    [Known Limitations](architecture/limitations.md) for what it does not do.

- **I am learning Kubernetes**

    ---

    [Core Concepts](concepts.md) covers the five ideas underneath this cluster —
    immutable OS, first-boot provisioning, sysexts, network boot, GitOps — and
    what each one costs.

- **Something is broken**

    ---

    [Operations](operations/index.md) has the health check and the
    symptom-to-cause table. If it is a stalled install, the
    [PXE troubleshooting table](quickstart.md#troubleshooting-pxe-boot).

</div>

## The stack in one table

| Layer | Choice | Why it is not the obvious one |
| --- | --- | --- |
| OS | [Flatcar Container Linux](concepts.md#flatcar-container-linux) | Read-only `/usr`. You cannot `apt install` your way out of a problem, which turns out to be the feature |
| Cluster | Kubernetes via kubeadm | Stock upstream, so the upstream docs apply verbatim |
| Network | [Cilium](platform/cilium.md) | eBPF, no `kube-proxy`, no iptables archaeology. Also supplies Gateway API and the LoadBalancer addresses bare metal does not come with |
| Storage | [Rook-Ceph](platform/rook-ceph.md) | Replicated block storage across the nodes, and the component most likely to teach you humility |
| Secrets | [OpenBao](platform/openbao.md) | Nothing sensitive in Git, at the price of a manual unseal after every reboot |
| Delivery | [ArgoCD](architecture/gitops.md) | If it is not in Git it is not real, and it will not survive the next reconcile |

Every one of those had a simpler alternative that was rejected on purpose. The
reasoning, and what each choice costs, is in
[Design Decisions](architecture/decisions.md).

## What is in each section

<div class="grid cards" markdown>

- **[Get Started](quickstart.md)**

    ---

    Concepts, the eleven-command build, and what to change before pointing any
    of it at your own hardware.

- **[Architecture](architecture/index.md)**

    ---

    The boot process arrow by arrow, the GitOps strategy, the security posture
    it assumes, and the limitations written down deliberately — because the ones
    you have not admitted to are the ones that page you.

- **[Platform](platform/index.md)**

    ---

    The fifteen components that make the cluster more than a very expensive way
    to run `nginx`, with the rollout order that lets a fresh bootstrap
    converge rather than deadlock.

- **[Operations](operations/index.md)**

    ---

    Health checks, node reboots, how updates actually get applied, and what is
    and is not backed up — the last of which is the only page here that will
    ever matter on your worst day.

- **[Development](development/index.md)**

    ---

    Working on the repository: the checks that run, how this site is built, what
    Renovate is allowed to merge, and how to add a workload.

</div>
