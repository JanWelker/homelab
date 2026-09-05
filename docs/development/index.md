---
description: "How to work on this repository — writing documentation, adding workloads, and the automation that keeps dependencies current."
---

# Development

How to work on this repository.

- **[Contributing](contributing.md)**: Repository setup, the checks that run,
    commit conventions, and validating a `payload/` change before you push.
- **[Documentation System](documentation.md)**: How this site is built with
    Zensical and published to GitHub Pages, and how to preview it locally.
- **[Maintenance](maintenance.md)**: The CI workflows that lint the repository
    and the Renovate configuration that keeps dependencies current.
- **[Adding a Workload](add-workload.md)**: Deploying a new application to the
    cluster, from the ArgoCD Application through to documenting it.

## Conventions

- Kubernetes manifests live under `payload/`, documentation under `docs/`.
- Everything in `payload/platform/` and `payload/workloads/` is applied by
    ArgoCD. Nothing is applied by hand after the initial bootstrap.
- Do not copy version numbers into prose. `targetRevision` in the manifests is
    the single source of truth, and Renovate keeps it current.
