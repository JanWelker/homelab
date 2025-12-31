# gateway-api

Kubernetes Gateway API resources.

## Components

- **Gateways**: `apps-gateway` for apps, `infra-gateway` for infrastructure.
- **HTTPRoutes**: Co-located with each app (not centralized).

## Directory Structure

```text
gateway-api/           # Gateway API Resources
├── crds.yaml          # ArgoCD Application for CRDs (v1.2.0)
└── gateways.yaml      # apps-gateway + infra-gateway
```
