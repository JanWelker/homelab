# Project Overview

![Homelab Logo](assets/images/logo.png){ align=right width=150 }

Welcome to the **Flatcar Homelab** documentation. This project provides a fully automated, GitOps-driven Kubernetes cluster on bare metal, leveraging Flatcar Container Linux and Kubeadm.

## Core Concepts

* **Immutable Infrastructure**: Uses Flatcar Container Linux for secure, auto-updating nodes.
* **GitOps**: All cluster state is managed via ArgoCD.
* **Networking**: Cilium for CNI, Gateway API, and WireGuard encryption.
* **Storage**: Rook-Ceph for distributed block storage.

## Documentation Sections

### [Quickstart Guide](quickstart.md)

Get your cluster up and running from scratch. Learn how to provision nodes, bootstrap the cluster, and install core components.

### [Platform Architecture](platform/index.md)

Deep dive into the core infrastructure components that power the cluster, including:

* [Cilium](platform/cilium.md) (Networking & Security)
* [Rook-Ceph](platform/rook-ceph.md) (Storage)
* [Monitoring](platform/monitoring.md) (Prometheus & Grafana)
* [Cert-Manager](platform/cert-manager.md) (TLS)

### [Workloads](workloads/index.md)

Explore the user-facing applications deployed on the cluster, such as:

* [Home Assistant](workloads/home-assistant.md)
* [Nextcloud](workloads/nextcloud.md)
* [Wichteln](workloads/wichteln.md)

### Developer Resources

* [Development](development/index.md#): Guides for contributing to the project.
* [Ansible](ansible/index.md#): Configuration management details.
