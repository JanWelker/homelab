---
description: "How to work on this repository — writing documentation, adding workloads, and the automation that keeps dependencies current."
---

# Development

How to work on this repository — and, more to the point, how to change it
without surprising the cluster.

<div class="grid cards" markdown>

- **[Contributing](contributing.md)**

    ---

    Repository setup, the checks that run, commit conventions, and how to
    validate a `payload/` change before you push it at real hardware.

- **[Adding a Workload](add-workload.md)**

    ---

    Deploying a new application, from the ArgoCD Application through to the
    documentation page the build will fail without.

- **[Documentation System](documentation.md)**

    ---

    How this site is built with Zensical and published, how to preview it
    locally, and the two markdownlint rules that will catch you.

- **[Maintenance](maintenance.md)**

    ---

    The CI workflows that lint the repository, and what Renovate is and is not
    allowed to merge on its own.

</div>

## Conventions

- Kubernetes manifests live under `payload/`, documentation under `docs/`.
- Everything in `payload/` is applied by ArgoCD. Nothing is applied by hand
    after the initial bootstrap.
- Workloads are not in this repository. They live in
    [`homelab-apps`](https://github.com/JanWelker/homelab-apps), which this
    repository references and never reads back — see
    [Adding a Workload](add-workload.md).
- Do not copy version numbers into prose. `targetRevision` in the manifests is
    the single source of truth, and Renovate keeps it current. A version written
    into a sentence is a version that will be wrong within a month and will stay
    wrong for a year.
