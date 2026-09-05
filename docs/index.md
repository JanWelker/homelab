---
description: "GitOps-driven Kubernetes homelab on bare metal, running Flatcar Container Linux, Kubeadm, Cilium, Rook-Ceph and ArgoCD."
---

# Project Overview

![Homelab Logo](assets/images/logo.png){ align=right width=150 }

Welcome to the **Flatcar Homelab** documentation. This project provides a fully automated, GitOps-driven Kubernetes cluster on bare metal, leveraging Flatcar Container Linux and Kubeadm.

## Core Concepts

- **Immutable Infrastructure**: Uses Flatcar Container Linux; updates stage automatically and are applied on a manual reboot.
- **GitOps**: All cluster state is managed via ArgoCD.
- **Networking**: Cilium for CNI, Gateway API, and WireGuard encryption.
- **Storage**: Rook-Ceph for distributed block storage.

## Documentation Sections

### [Quickstart](quickstart.md)

Get your cluster up and running from scratch. Learn how to provision nodes, bootstrap the cluster, and install core components.

### [Adapting This for Your Cluster](adapting.md)

This repository describes one specific homelab. What to change — repository URL, domain, addresses, hardware — before pointing any of it at your own machines.

### [Architecture](architecture/index.md)

How the cluster is put together: the [boot process](architecture/boot-process.md), the [GitOps strategy](architecture/gitops.md), and the [directory structure](architecture/directory-structure.md).

### [Platform](platform/index.md)

Deep dive into the core infrastructure components that power the cluster, including:

- [Cilium](platform/cilium.md) (Networking & Security)
- [Rook-Ceph](platform/rook-ceph.md) (Storage)
- [Monitoring](platform/monitoring.md) (Prometheus & Grafana)
- [cert-manager](platform/cert-manager.md) (TLS)

### [Operations](operations/index.md)

Running the cluster once it is up: health checks, [rebooting nodes](operations/index.md#rebooting-a-node), [how updates actually get applied](operations/upgrades.md), and [what is and is not backed up](operations/backups.md).

### [Workloads](workloads/index.md)

Explore the user-facing applications deployed on the cluster.

### [Development](development/index.md)

- [Documentation System](development/documentation.md): How the docs site is built and deployed.
- [Maintenance](development/maintenance.md): CI/CD workflows and dependency management.
- [Adding a Workload](development/add-workload.md): Step-by-step guide for deploying a new application.

### Machine Provisioning

- [Boot Server](boot_server/index.md): The PXE boot server that serves Flatcar and Ignition to bare metal nodes.
- [Ansible](ansible/index.md): Configuration management details.
