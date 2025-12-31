# home-assistant

Home automation platform.

## Deployment

Deployed via Helm Chart (inferred from lack of raw manifests and large `application.yaml`).

## Components

- **Application**: `payload/workloads/home-assistant/application.yaml` - ArgoCD Application definition.
- **HTTPRoute**: `payload/workloads/home-assistant/httproute.yaml` - Exposes the UI.

## Access

Access the Home Assistant dashboard via the configured HTTPRoute.
