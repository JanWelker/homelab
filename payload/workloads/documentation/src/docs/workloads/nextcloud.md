# nextcloud

File hosting and productivity service.

## Deployment

Deployed via Helm Chart.

## Components

- **Application**: `payload/workloads/nextcloud/application.yaml` - ArgoCD Application definition.
- **HTTPRoute**: `payload/workloads/nextcloud/httproute.yaml` - Exposes the Nextcloud interface.

## Access

Access your files via the configured HTTPRoute.
