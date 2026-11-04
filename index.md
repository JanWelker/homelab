# Flatcar Homelab

![Homelab Logo](<https://homelab.wlkr.ch/assets/images/logo.png>)

A rack of machines and a pile of YAML: a bare metal Kubernetes cluster that provisions itself over PXE, keeps its entire state in Git, and is documented here so that nothing has to be re-derived at 02:00.

It is a real cluster doing real work, built the way a production cluster is built, at a scale where breaking it is a learning experience rather than an incident review.

## Start here

- **I want to build this**

  ---

  [Adapt It to Your Cluster](<https://homelab.wlkr.ch/adapting/index.md>) first — the repository URL, domain and addresses are hardcoded — then the [Quickstart](<https://homelab.wlkr.ch/quickstart/index.md>). Set aside an afternoon.
- **I know Kubernetes, not this repo**

  ---

  [Architecture](<https://homelab.wlkr.ch/architecture/index.md>) for how it fits together, [Design Decisions](<https://homelab.wlkr.ch/architecture/decisions/index.md>) for why, and [Known Limitations](<https://homelab.wlkr.ch/architecture/limitations/index.md>) for what it does not do.
- **I am learning Kubernetes**

  ---

  [Core Concepts](<https://homelab.wlkr.ch/concepts/index.md>) covers the five ideas underneath this cluster — immutable OS, first-boot provisioning, sysexts, network boot, GitOps — and what each one costs.
- **Something is broken**

  ---

  [Operations](<https://homelab.wlkr.ch/operations/index.md>) has the health check and the symptom-to-cause table. If it is a stalled install, the [PXE troubleshooting table](<https://homelab.wlkr.ch/quickstart/#troubleshooting-pxe-boot>).
- **I am making a change**

  ---

  [Development](<https://homelab.wlkr.ch/development/index.md>) for the checks a pull request must pass, [GitOps Strategy](<https://homelab.wlkr.ch/architecture/gitops/index.md>) for what a merge does to the cluster, and the [repository layout](<https://homelab.wlkr.ch/architecture/#repository-layout>) for where things live.

## The stack in one table

Each choice had a simpler alternative that was rejected on purpose; the reasoning and what each one costs is in [Design Decisions](<https://homelab.wlkr.ch/architecture/decisions/index.md>).

| Layer | Choice | Why it is not the obvious one |
| --- | --- | --- |
| OS | [Flatcar Container Linux](<https://homelab.wlkr.ch/concepts/#flatcar-container-linux>) | Read-only `/usr`. You cannot `apt install` your way out of a problem, which turns out to be the feature |
| Cluster | Kubernetes via kubeadm | Stock upstream, so the upstream docs apply verbatim |
| Network | [Cilium](<https://homelab.wlkr.ch/platform/cilium/index.md>) | eBPF, no `kube-proxy`, no iptables archaeology. Also supplies Gateway API and the LoadBalancer addresses bare metal does not come with |
| Storage | [Rook-Ceph](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) | Replicated block storage across the nodes, and the component most likely to teach you humility |
| Secrets | [OpenBao](<https://homelab.wlkr.ch/platform/openbao/index.md>) | Nothing sensitive in Git, at the price of a manual unseal after every reboot |
| Delivery | [ArgoCD](<https://homelab.wlkr.ch/architecture/gitops/index.md>) | If it is not in Git it is not real, and it will not survive the next reconcile |

The sections follow the reader's journey: [Get Started](<https://homelab.wlkr.ch/quickstart/index.md>) to build it, [Architecture](<https://homelab.wlkr.ch/architecture/index.md>) to understand it, [Platform](<https://homelab.wlkr.ch/platform/index.md>) for each of the components, [Operations](<https://homelab.wlkr.ch/operations/index.md>) to run it, and [Development](<https://homelab.wlkr.ch/development/index.md>) to work on the repository.
