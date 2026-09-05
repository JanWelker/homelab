# Architecture Overview

This project provides a mechanism to deploy a Bare Metal Kubernetes Cluster using
Flatcar Container Linux and Kubeadm.

## Key Concepts

If you're new to this stack, the following concepts are worth understanding before diving in.

### Flatcar Container Linux

[Flatcar](https://www.flatcar.org/) is an immutable, minimal Linux distribution designed specifically for running containers. The root filesystem is read-only — you cannot install packages or modify system files at runtime. This forces all configuration to happen declaratively at first boot via **Ignition**.

Flatcar automatically applies OS updates in the background and uses an A/B partition scheme to roll back if an update fails.

### Ignition & Butane

**Ignition** is Flatcar's first-boot provisioning system. It reads a JSON config on first boot and applies it: creates users, writes files, partitions disks, enables systemd units, etc.

**Butane** is a human-friendly YAML format that compiles down to Ignition JSON. In this project, Ansible generates Butane configs from Jinja2 templates, then transpiles them to Ignition JSON which the boot server serves over HTTP.

### Systemd Sysexts

Because Flatcar's root filesystem is read-only, software like `kubernetes` and `containerd` is delivered as **system extensions** (sysexts) — read-only overlay images that extend `/usr` at boot time via `systemd-sysupdate`. The nodes download these from the HTTP boot server on first boot.

### PXE Boot

**PXE (Preboot Execution Environment)** allows machines to boot from the network instead of a local disk. The NIC requests a boot file from a TFTP server (pointed to by the DHCP server). In this project:

- DHCP hands out the boot server IP and a syslinux filename
- TFTP serves the syslinux bootloader
- Syslinux fetches the Flatcar kernel + initrd over HTTP, passing the Ignition config URL as a kernel parameter

## Components

### 1. Deployment Host (Local Machine)

The machine where this project is executed.

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

**Networks**: Pod subnet `10.244.0.0/16`, Service subnet `10.96.0.0/12`

## Technologies

| Layer | Tool | Purpose |
| --- | --- | --- |
| OS | Flatcar Container Linux | Immutable, auto-updating container OS |
| Orchestration | Kubernetes (Kubeadm) | Container scheduling and management |
| CNI | Cilium (eBPF) | Networking, kube-proxy replacement |
| Ingress | Gateway API (via Cilium) | HTTP/HTTPS traffic routing |
| GitOps | ArgoCD | Declarative cluster state management |
| Storage | Rook-Ceph | Distributed block storage |
| Config Gen | Ansible + Jinja2 | Per-node config generation |
| Boot Serving | Python (HTTP + TFTP) | PXE boot artifacts |
