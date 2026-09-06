---
description: "GitOps-driven Kubernetes homelab on bare metal, running Flatcar Container Linux, Kubeadm, Cilium, Rook-Ceph and ArgoCD."
---

# Project Overview

![Homelab Logo](assets/images/logo.png){ align=right width=150 }

Six machines in a rack, a pile of YAML, and a stubborn refusal to pay a cloud
provider for something that fits under a desk. This is the **Flatcar Homelab**:
a bare metal Kubernetes cluster that provisions itself over PXE, keeps its
entire state in Git, and is documented here mostly so that Future Me, at 02:00,
with one hand holding a phone flashlight, does not have to re-derive any of it.

It is a real cluster doing real work, built the way a production cluster is
built, at a scale where breaking it is a learning experience rather than an
incident review.

## Core Concepts

- **Immutable Infrastructure**: Flatcar Container Linux. You cannot `apt install`
  your way out of a problem, which turns out to be the feature.
- **GitOps**: All cluster state lives in ArgoCD. If it is not in Git, it is not
  real, and it will not survive the next reconcile.
- **Networking**: Cilium for CNI, Gateway API, and WireGuard encryption. No
  `kube-proxy`, no iptables archaeology.
- **Storage**: Rook-Ceph. Distributed block storage, and the component most
  likely to teach you humility.

## Documentation Sections

### [Quickstart](quickstart.md)

Get your cluster up and running from scratch. Provision nodes, bootstrap the
cluster, install the core components — and find out which step everyone gets
wrong the first time (it's the boot server IP; it's always the boot server IP).

### [Adapting This for Your Cluster](adapting.md)

This repository describes one specific homelab. What to change — repository URL,
domain, addresses, hardware — before pointing any of it at your own machines.
Read this before you accidentally build a cluster that faithfully syncs somebody
else's opinions.

### [Architecture](architecture/index.md)

How the cluster is put together: the [boot process](architecture/boot-process.md),
the [GitOps strategy](architecture/gitops.md), the
[directory structure](architecture/directory-structure.md), the
[security posture](architecture/security.md) it assumes, the
[decisions](architecture/decisions.md) behind the stack, and its
[known limitations](architecture/limitations.md) — written down deliberately,
because the limitations you have not admitted to are the ones that page you.

### [Platform](platform/index.md)

The core infrastructure that makes the cluster more than a very expensive way to
run `nginx`:

- [Cilium](platform/cilium.md) (Networking & Security)
- [Rook-Ceph](platform/rook-ceph.md) (Storage)
- [Monitoring](platform/monitoring.md) (Prometheus & Grafana)
- [cert-manager](platform/cert-manager.md) (TLS)

### [Operations](operations/index.md)

Running the cluster once it is up: health checks,
[rebooting nodes](operations/index.md#rebooting-a-node),
[how updates actually get applied](operations/upgrades.md), and
[what is and is not backed up](operations/backups.md) — the last of which is the
only page here that will ever matter on your worst day.

### [Workloads](workloads/index.md)

The user-facing applications. Currently a quiet section, which is the natural
state of a homelab that has just finished rebuilding its platform for the third
time.

### [Development](development/index.md)

- [Contributing](development/contributing.md): Setup, checks, and conventions for
  working on this repository.
- [Documentation System](development/documentation.md): How this site is built
  and deployed.
- [Maintenance](development/maintenance.md): CI/CD workflows and dependency
  management.
- [Adding a Workload](development/add-workload.md): Step-by-step guide for
  deploying a new application.

### Machine Provisioning

- [Boot Server](boot_server/index.md): The PXE boot server that serves Flatcar
  and Ignition to bare metal nodes.
- [Ansible](ansible/index.md): Configuration management details.
