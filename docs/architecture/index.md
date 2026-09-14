---
description: "How the cluster is put together: hardware, node roles, networking, and the design decisions behind the homelab."
---

# Architecture

This project deploys a bare metal Kubernetes cluster using Flatcar Container
Linux and Kubeadm. No cloud provider, no managed control plane, no friendly
button labelled "create cluster" — just six machines, a network, and a
deployment host that talks them into existence.

This page assumes you know what Flatcar, Ignition, systemd sysexts, PXE and
GitOps are. If any of those is new, [Core Concepts](../concepts.md) covers all
five in one page.

## What is in this section

<div class="grid cards" markdown>

- **[Boot & Bootstrap Process](boot-process.md)**

    ---

    One node from power-on to `kubectl get nodes`, arrow by arrow. The page to
    read when an install stalls, because it turns "it hangs" into "which arrow
    did not happen?"

- **[Boot Server](boot-server.md)** · **[Ansible](ansible.md)**

    ---

    The two halves of the deployment host: the Python script that serves TFTP
    and HTTP to a booting node, and the playbooks that generate everything it
    serves.

- **[GitOps Strategy](gitops.md)**

    ---

    App-of-Apps, and the sync waves that make a fresh bootstrap converge instead
    of deadlocking on a CRD that does not exist yet.

- **[Design Decisions](decisions.md)**

    ---

    Seven choices, each stated as *X, not Y*, each with the cost it carries.
    Read this before proposing a simpler alternative — it is probably in here.

- **[Security Posture](security.md)**

    ---

    The trust boundary this cluster assumes, what is deliberately not enforced,
    and the audit logging that records the rest.

- **[Known Limitations](limitations.md)**

    ---

    What it does not do, written down so it is findable before it is discovered.

- **[Repository Layout](directory-structure.md)**

    ---

    Which of the four top-level directories to be in, and which one never to
    edit by hand.

</div>

## Components

### 1. Deployment Host (Local Machine)

The machine where this project is executed. Notably **not** part of the cluster —
it is a laptop on the same network, and the cluster does not depend on it once
provisioning is done.

- **Ansible**: Responsible for generating the configuration files (Ignition,
  Kubeadm config) based on templates and variables.
- **Python Boot Server**: A custom Python script that runs:
  - **TFTP Server**: Serves the Bootloader (syslinux.efi/lpxelinux.0) and config.
  - **HTTP Server**: Serves Ignition configs, Flatcar Kernel/Initrd, and Sysext
    images (`.raw`) + configs (`.conf`).
- **Artifacts**: Directory containing downloaded OS images (Flatcar) and
  generated configs.

### 2. Target Host (Bare Metal Node)

The physical machine to be provisioned.

- **PXE Client**: NIC boots via network (DHCP provided externally).
- **Flatcar OS**: The operating system loaded into RAM and then installed to disk.
- **Kubeadm**: The tool used to bootstrap the Kubernetes cluster.

## Cluster Layout

| Node | Role | IP |
| --- | --- | --- |
| odin | Control Plane | 10.9.2.1 |
| thor | Control Plane | 10.9.2.2 |
| loki | Control Plane | 10.9.2.3 |
| freya | Worker | 10.9.2.4 |
| heimdall | Worker | 10.9.2.5 |
| valkyrie | Worker | 10.9.2.6 |

Three control-plane nodes, because etcd needs an odd number and two is the worst
possible answer: twice the hardware, and you still lose quorum when one dies.

The API server is reached through a kube-vip virtual IP (`10.9.2.10` by
default) rather than any single node — see
[Control Plane VIP](../operations/control-plane-vip.md). Naming a node as the
API endpoint works fine right up until that node is the one you need to reboot.

**Networks**: Pod subnet `10.244.0.0/16`, Service subnet `10.96.0.0/12`

## Technologies

| Layer | Tool | Purpose |
| --- | --- | --- |
| OS | Flatcar Container Linux | Immutable container OS, updated via A/B partitions |
| Orchestration | Kubernetes (Kubeadm) | Container scheduling and management |
| CNI | Cilium (eBPF) | Networking, kube-proxy replacement |
| Ingress | Gateway API (via Cilium) | HTTP/HTTPS traffic routing |
| GitOps | ArgoCD | Declarative cluster state management |
| Storage | Rook-Ceph | Distributed block storage |
| Config Gen | Ansible + Jinja2 | Per-node config generation |
| Boot Serving | Python (HTTP + TFTP) | PXE boot artifacts |
| API HA | kube-vip (ARP) | Virtual IP in front of the API servers |

Every one of these had a simpler alternative that was rejected on purpose. The
reasoning, including what each choice costs, is in
[Design Decisions](decisions.md).
