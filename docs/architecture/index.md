---
description: "How the cluster is put together: node roles, networking, the repository layout, and where the design decisions live."
---

# Architecture

A bare metal Kubernetes cluster on Flatcar Container Linux and kubeadm: a rack
of machines, a network, and a deployment host that talks them into existence. The
stack itself is summarised in [one table](../index.md#the-stack-in-one-table)
on the home page, and the reasoning behind each choice in
[Design Decisions](decisions.md).

This section assumes you know what Flatcar, Ignition, systemd sysexts, PXE and
GitOps are; [Core Concepts](../concepts.md) covers all five.

## What is in this section

<div class="grid cards" markdown>

- **[Boot & Bootstrap Process](boot-process.md)**

    ---

    One node from power-on to `kubectl get nodes`, arrow by arrow, and the
    boot server that serves each step. Start here when an install stalls.

- **[Ansible](ansible.md)**

    ---

    The playbooks that generate everything the boot server serves. Ansible
    never configures a node.

- **[GitOps Strategy](gitops.md)**

    ---

    The ApplicationSet, the sync policy every Application shares, and how a
    fresh bootstrap converges without an order.

- **[Design Decisions](decisions.md)**

    ---

    Each choice as *X, not Y*, with its cost. Read before proposing a
    simpler alternative.

- **[Security Posture](security.md)** · **[Audit Logging](audit-logging.md)**

    ---

    The trust boundary this cluster assumes, what is deliberately not
    enforced, and what the API server records.

- **[Known Limitations](limitations.md)**

    ---

    What it does not do, in one table.

</div>

## Cluster Layout

The deployment host — the laptop running Ansible and the boot server — is
**not** part of the cluster and nothing depends on it once provisioning is done.

| Role | Nodes | Purpose |
| --- | --- | --- |
| Control plane | odin, thor, loki | etcd and the API server; tainted, no workloads, no Ceph OSD |
| Worker | freya, heimdall, valkyrie, baldur, tyr, frigg, bragi, idun | Everything else, and a Ceph OSD partition each |

Three control-plane nodes because etcd needs an odd number, and two is the
worst answer: twice the hardware and quorum still lost when one dies. The API
server is reached through a kube-vip virtual IP rather than any single node —
see [Control Plane VIP](../operations/control-plane-vip.md). Addresses, the
VIP and the pod and service subnets are in `ansible/inventory.yaml`.

## Repository layout

`ansible/` describes the machines, `boot_server/` hands them their operating
system, `payload/` is the platform the cluster runs on, and `output/` is
generated — never edit anything in there, the next `make config` overwrites
it. The applications the cluster exists to serve live in the separate
[`homelab-apps`](https://github.com/JanWelker/homelab-apps) repository; see
[GitOps Strategy](gitops.md#workloads-live-in-a-second-repository).

```text
.
├── .github/workflows       # CI: linters, docs build and preview, argo-diff-preview -- see Maintenance
├── ansible                 # Inventory, playbooks and templates -- see Ansible
├── boot_server
│   └── serve.py            # TFTP + HTTP boot server
├── docs                    # This site
├── output                  # Generated; never edited by hand
│   ├── credentials/        # Bootstrap token, certificate key, etcd
│   │                       # encryption key, OpenBao unseal keys -- 0700
│   ├── http/               # Ignition, Flatcar artifacts, sysext images
│   ├── kubeconfig          # Admin kubeconfig
│   ├── router/             # FRR BGP config for the router
│   ├── tftp/               # PXE bootloader and menus
│   └── tmp/                # Readable copies of the rendered Butane and kubeadm configs
├── overrides               # Theme template overrides for the docs site
├── payload                 # Everything ArgoCD deploys
│   ├── argocd/             # ArgoCD itself: Application, ApplicationSet, values
│   ├── platform/           # One directory per component, each with one
│   │   └── ...             #   application.yaml -- see Platform
│   └── workloads/          # The handover to homelab-apps
├── scripts                 # What the make targets run: OpenBao bootstrap, storage check, fonts
├── tests                   # pytest for the boot server
├── Makefile                # Every operator command, one target each
├── renovate.json           # The version pins Renovate tracks -- see Maintenance
├── zensical.toml           # Documentation site configuration
└── README.md
```

`ansible/` and `boot_server/` matter only while a node is being built;
`payload/` matters every day after that. The exception is
`ansible/templates/kubeadm.yaml.j2`, which explains why half the control plane
is configured the way it is.

`output/credentials/` is generated once and read back on every later run;
`make config` writes new values for anything missing, which a running cluster
will not accept. `make clean` therefore prints that inventory and asks before
deleting, and refuses without a terminal.

| Target | Removes | Keeps |
| --- | --- | --- |
| `make clean-artifacts` | `output/http`, `output/tftp`, `output/tmp`, `output/router` | `output/credentials/`, `output/kubeconfig` |
| `make clean` | everything under `output/` | nothing — it asks first |
