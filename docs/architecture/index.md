---
description: "How the cluster is put together: hardware, node roles, networking, and the design decisions behind the homelab."
---

# Architecture Overview

This project deploys a bare metal Kubernetes cluster using Flatcar Container
Linux and Kubeadm. No cloud provider, no managed control plane, no friendly
button labelled "create cluster" — just six machines, a network, and a
deployment host that talks them into existence.

## Key Concepts

If you're new to this stack, the following concepts are worth understanding
before diving in. They are also the four things that, once they click, make the
rest of this documentation stop feeling like a list of unrelated tools.

### Flatcar Container Linux

[Flatcar](https://www.flatcar.org/) is an immutable, minimal Linux distribution designed specifically for running containers. The root filesystem is read-only — you cannot install packages or modify system files at runtime. This forces all configuration to happen declaratively at first boot via **Ignition**.

That constraint is the entire point. Anyone who has inherited a fleet of
"identical" servers knows they are identical the way siblings are: broadly
similar, differing in ways nobody wrote down, and each carrying one undocumented
fix applied at 3am by someone who has since left. A read-only `/usr` makes that
impossible rather than merely discouraged.

Flatcar downloads OS updates in the background into an A/B partition pair, so a failed update can be rolled back. This project masks `locksmithd`, the service that would normally coordinate the reboot, so a staged update is applied only when [Kured](../platform/kured.md) drains the node — or when you reboot it yourself. See [Updates & Upgrades](../operations/upgrades.md).

### Ignition & Butane

**Ignition** is Flatcar's first-boot provisioning system. It reads a JSON config on first boot and applies it: creates users, writes files, partitions disks, enables systemd units, etc.

**Butane** is a human-friendly YAML format that compiles down to Ignition JSON. In this project, Ansible generates Butane configs from Jinja2 templates, then transpiles them to Ignition JSON which the boot server serves over HTTP.

Ignition runs *once*, in the initramfs, before the real root is mounted. It is
not a configuration management system and it will not converge anything on the
second boot. If you change a template, the node has to be reprovisioned to care.

### Systemd Sysexts

Because Flatcar's root filesystem is read-only, software like `kubernetes` and `containerd` is delivered as **system extensions** (sysexts) — read-only overlay images that extend `/usr` at boot time via `systemd-sysupdate`. The nodes download these from the HTTP boot server on first boot.

This is where "immutable OS" stops being an abstract virtue and starts being a
thing you have to reason about: upgrading Kubernetes on these nodes is not
`apt upgrade`, it is swapping a squashfs image and rebooting. See
[Updates & Upgrades](../operations/upgrades.md) for how that actually plays out.

### PXE Boot

**PXE (Preboot Execution Environment)** allows machines to boot from the network instead of a local disk. The NIC requests a boot file from a TFTP server (pointed to by the DHCP server). In this project:

- DHCP hands out the boot server IP and a syslinux filename
- TFTP serves the syslinux bootloader
- Syslinux fetches the Flatcar kernel + initrd over HTTP, passing the Ignition config URL as a kernel parameter

PXE is a protocol from 1998 that runs over UDP with no error correction worth
the name, which is why everything larger than the bootloader moves over HTTP as
fast as possible. Respect it; it has outlived most of the things designed to
replace it.

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
