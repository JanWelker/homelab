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
**[https://homelab.wlkr.ch/](https://homelab.wlkr.ch/)**.

- **[Quickstart Guide](https://homelab.wlkr.ch/quickstart/)**: Instructions for bootstrapping the cluster.
- **[Adapting This for Your Cluster](https://homelab.wlkr.ch/adapting/)**: What to change before running this against your own hardware.
- **[Platform](https://homelab.wlkr.ch/platform/)**: Details on core infrastructure components.
- **[Adding a Workload](https://homelab.wlkr.ch/development/add-workload/)**: How to deploy an application onto the cluster.

## Repository Structure

- `ansible/`: Ansible playbooks for bootstrapping and configuration generation.
- `boot_server/`: Python-based PXE boot server.
- `docs/`: Documentation sources, built with [Zensical](https://zensical.org/)
  (configured in `zensical.toml`) and published to GitHub Pages.
- `payload/`: The "GitOps Payload" containing ArgoCD Applications and Kubernetes manifests.
  - `platform/`: Core infrastructure (Cilium, Rook, etc.).
  - `argocd/`: ArgoCD bootstrap configuration.
  - `workloads/`: The ApplicationSet that hands over to the workloads repository.

The applications the cluster runs live in a second repository,
[JanWelker/homelab-apps](https://github.com/JanWelker/homelab-apps); this one
references it once, from the `apps` ApplicationSet, and never reads it back.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), or the
[Contributing](https://homelab.wlkr.ch/development/contributing/)
page for setup, checks, commit conventions, and how to validate a `payload/`
change before opening a pull request.
