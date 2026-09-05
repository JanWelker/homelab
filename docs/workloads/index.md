# Workloads

User-facing applications deployed to the cluster live under
`payload/workloads/<app-name>/`.

No workloads are currently deployed. The documentation site — previously the
only workload — is now built with [Zensical](https://zensical.org/) and
published to GitHub Pages instead of being served from the cluster. See
[Documentation System](../development/documentation.md).

## Management

Because there are no workloads, neither `payload/workloads/` nor its `workloads`
parent ArgoCD Application currently exists. Adding the first one restores both;
see [Adding a Workload](../development/add-workload.md).

Once the parent Application is in place, each app folder contains its own ArgoCD
`application.yaml` (auto-discovered by the parent App-of-Apps) and the
Kubernetes manifests (Deployment, Service, HTTPRoute, etc.) or Helm chart
configuration. Workloads sync after the core platform infrastructure is ready.
