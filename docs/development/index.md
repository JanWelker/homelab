---
description: "How to work on this repository: contributing, adding workloads, and the automation that keeps dependencies current."
---

# Development

How to change this repository without surprising the cluster. Manifests live
under `payload/` and are applied by ArgoCD; documentation lives under `docs/`;
workloads live in [`homelab-apps`](https://github.com/JanWelker/homelab-apps).

<div class="grid cards" markdown>

- **[Contributing](contributing.md)**

    ---

    Repository setup, the checks to run, commit conventions, and how the
    documentation site is built and published.

- **[Adding a Workload](add-workload.md)**

    ---

    Deploying a new application, from the ArgoCD Application through to the
    documentation page the build fails without.

- **[Adding a Platform Component](add-platform-component.md)**

    ---

    A new directory under `payload/platform/`, and everything a component
    needs before its first sync: the Application, the policies, the pin and
    the page.

- **[Maintenance](maintenance.md)**

    ---

    The CI workflows, and what Renovate is and is not allowed to merge on its
    own.

</div>
