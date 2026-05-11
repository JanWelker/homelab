# Workloads

This directory contains the user-facing applications deployed to the cluster.
These are managed by the `workloads` parent application in ArgoCD.

No workloads are currently deployed.

## Management

Each application folder contains its own ArgoCD `application.yaml` (managed by
the parent App-of-Apps) and the Kubernetes manifests (Deployment, Service,
HTTPRoute, etc.) or Helm Chart configuration.

All applications are part of the `workloads` sync wave group and are deployed
after the core platform infrastructure is ready.
