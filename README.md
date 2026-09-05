# Flatcar Kubernetes Homelab

![Homelab Logo](docs/assets/images/logo.png)

Welcome to the **Flatcar Homelab** project. This repository contains the configuration and automation for a fully automated, GitOps-driven Kubernetes cluster on bare metal, leveraging Flatcar Container Linux and Kubeadm.

## Core Concepts

- **Immutable Infrastructure**: Uses Flatcar Container Linux; updates stage automatically and are applied on a manual reboot.
- **GitOps**: All cluster state is managed via ArgoCD.
- **Networking**: Cilium for CNI, Gateway API, and WireGuard encryption.
- **Storage**: Rook-Ceph for distributed block storage.

## Documentation

The full project documentation is published to GitHub Pages at
**[https://janwelker.github.io/homelab/](https://janwelker.github.io/homelab/)**.

- **[Quickstart Guide](https://janwelker.github.io/homelab/quickstart/)**: Instructions for bootstrapping the cluster.
- **[Adapting This for Your Cluster](https://janwelker.github.io/homelab/adapting/)**: What to change before running this against your own hardware.
- **[Platform](https://janwelker.github.io/homelab/platform/)**: Details on core infrastructure components.
- **[Workloads](https://janwelker.github.io/homelab/workloads/)**: Information about deployed applications.

## Repository Structure

- `ansible/`: Ansible playbooks for bootstrapping and configuration generation.
- `boot_server/`: Python-based PXE boot server.
- `docs/`: Documentation sources, built with [Zensical](https://zensical.org/)
  (configured in `zensical.toml`) and published to GitHub Pages.
- `payload/`: The "GitOps Payload" containing ArgoCD Applications and Kubernetes manifests.
  - `platform/`: Core infrastructure (Cilium, Rook, etc.).
  - `argocd/`: ArgoCD bootstrap configuration.

## Contributing

See the [Development](https://janwelker.github.io/homelab/development/documentation/)
section of the documentation for contribution guidelines and development workflows.
