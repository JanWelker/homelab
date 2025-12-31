# cilium

CNI with Gateway API, WireGuard encryption, and L2 announcements.

## Components

- **Gateway API**: Replaces traditional Ingress controller.
- **LoadBalancer Pools**: `10.9.2.249` (apps) and `10.9.2.248` (infra).
- **Hubble**: Observability with metrics and UI at `hubble.infra.k8s.wlkr.ch`.

## Directory Structure

```text
cilium/                # CNI + Gateway API Controller
├── application.yaml   # ArgoCD Application (Helm v1.18.5)
├── values.yaml        # Helm values
├── lb-pools.yaml      # CiliumLoadBalancerIPPool + L2 Policy
├── rbac-gateway-fix.yaml # RBAC fix for Gateway API
└── httproute.yaml     # Hubble UI route
```
