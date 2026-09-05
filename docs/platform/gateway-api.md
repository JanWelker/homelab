# Gateway API

The [Kubernetes Gateway API](https://gateway-api.sigs.k8s.io/) replaces the traditional Ingress resource. It is implemented by Cilium, which handles both load balancing and TLS termination.

## Gateways

Two Gateways are defined in `kube-system`, each with a dedicated IP from the Cilium L2 pool:

| Gateway | IP | Hostname pattern | Used for |
| --- | --- | --- | --- |
| `apps-gateway` | `10.9.2.249` | `*.k8s.wlkr.ch` | User-facing workloads |
| `infra-gateway` | `10.9.2.248` | `*.infra.k8s.wlkr.ch` | Platform services (Grafana, Hubble, etc.) |

Both gateways terminate TLS using wildcard certificates managed by cert-manager. HTTP traffic is accepted on port 80 from all namespaces (for redirect purposes).

## Exposing a New Service

To expose a service, create an `HTTPRoute` in the same namespace as your app and reference the appropriate gateway.

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-app
  namespace: my-app
spec:
  parentRefs:
    - name: apps-gateway
      namespace: kube-system
  hostnames:
    - "my-app.k8s.wlkr.ch"
  rules:
    - backendRefs:
        - name: my-app-svc
          port: 80
```

Use `infra-gateway` with a `*.infra.k8s.wlkr.ch` hostname for internal platform tools instead.

## Directory Structure

```text
gateway-api/           # Gateway API Resources
├── crds.yaml          # ArgoCD Application for CRDs (v1.2.0)
└── gateways.yaml      # apps-gateway + infra-gateway
```
