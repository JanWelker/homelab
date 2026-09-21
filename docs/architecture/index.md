---
description: "How the cluster is put together: node roles, networking, the repository layout, and where the design decisions live."
---

# Architecture

A bare metal Kubernetes cluster on Flatcar Container Linux and kubeadm: six
machines, a network, and a deployment host that talks them into existence. The
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

    The ApplicationSet, the staged rollout, and the rules that keep a fresh
    bootstrap from deadlocking.

- **[Design Decisions](decisions.md)**

    ---

    Seven choices, each as *X, not Y*, each with its cost. Read before
    proposing a simpler alternative.

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

| Node | Role | IP |
| --- | --- | --- |
| odin | Control Plane | 10.9.2.1 |
| thor | Control Plane | 10.9.2.2 |
| loki | Control Plane | 10.9.2.3 |
| freya | Worker | 10.9.2.4 |
| heimdall | Worker | 10.9.2.5 |
| valkyrie | Worker | 10.9.2.6 |

Three control-plane nodes because etcd needs an odd number, and two is the
worst answer: twice the hardware and quorum still lost when one dies. The API
server is reached through a kube-vip virtual IP (`10.9.2.10` by default) rather
than any single node — see
[Control Plane VIP](../operations/control-plane-vip.md).

**Networks**: pod subnet `10.244.0.0/16`, service subnet `10.96.0.0/12`.

## Repository layout

`ansible/` describes the machines, `boot_server/` hands them their operating
system, `payload/` is the platform the cluster runs on, and `output/` is
generated — never edit anything in there, the next `make config` overwrites
it. The applications the cluster exists to serve live in the separate
[`homelab-apps`](https://github.com/JanWelker/homelab-apps) repository; see
[GitOps Strategy](gitops.md#workloads-live-in-a-second-repository).

```text
.
├── ansible                 # Inventory, playbooks and templates -- see Ansible
├── boot_server
│   └── serve.py            # TFTP + HTTP boot server
├── docs                    # This site
├── output                  # Generated; never edited by hand
│   ├── credentials/        # Bootstrap token, certificate key, etcd
│   │                       # encryption key, OpenBao unseal keys -- 0700
│   ├── http/               # Ignition, Flatcar artifacts, sysext images
│   ├── kubeconfig          # Admin kubeconfig
│   ├── tftp/               # PXE bootloader and menus
│   └── tmp/                # Temporary workspace
├── payload                 # Everything ArgoCD deploys
│   ├── argocd/             # ArgoCD itself: Application, ApplicationSet, values
│   ├── platform/           # One directory per component, each with one
│   │   └── ...             #   application.yaml -- see Platform
│   └── workloads/          # The handover to homelab-apps, stage 12-workloads
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
| `make clean-artifacts` | `output/http`, `output/tftp`, `output/tmp` | `output/credentials/`, `output/kubeconfig` |
| `make clean` | everything under `output/` | nothing — it asks first |
