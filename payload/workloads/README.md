# Workloads

This directory contains the user-facing applications deployed to the cluster.
These are managed by the `workloads` parent application in ArgoCD.

## Applications

| Application | Description |
| :--- | :--- |
| **advent-wollbi** | A custom application for the Advent season. |
| **github-actions-runner** | Self-hosted GitHub Actions runners (ARC). |
| **home-assistant** | Home automation platform. |
| **nextcloud** | File hosting and productivity service. |
| **nginx-test** | A simple Nginx application for testing deployment flows. |

## Management

Each application folder contains its own ArgoCD `application.yaml` (managed by
the parent App-of-Apps) and the Kubernetes manifests (Deployment, Service,
HTTPRoute, etc.) or Helm Chart configuration.

All applications are part of the `workloads` sync wave group and are deployed
after the core platform infrastructure is ready.
