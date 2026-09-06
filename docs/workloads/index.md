---
description: "User-facing applications deployed to the cluster and how the workloads parent Application manages them."
---

# Workloads

User-facing applications deployed to the cluster live under
`payload/workloads/<app-name>/`.

No workloads are currently deployed. The documentation site is built with
[Zensical](https://zensical.org/) and published to GitHub Pages rather than
served from the cluster — see
[Documentation System](../development/documentation.md).

## Management

Because there are no workloads, neither `payload/workloads/` nor its `workloads`
parent ArgoCD Application currently exists. Adding the first workload creates
both; see [Adding a Workload](../development/add-workload.md).

Once the parent Application is in place, each app folder contains its own ArgoCD
`application.yaml` (auto-discovered by the parent App-of-Apps) and the
Kubernetes manifests (Deployment, Service, HTTPRoute, etc.) or Helm chart
configuration. Workloads sync after the core platform infrastructure is ready.
