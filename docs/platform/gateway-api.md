---
description: "Gateway API resources: the apps and infra Gateways, HTTP to HTTPS redirection, and how to expose a new service."
---

# Gateway API

The [Kubernetes Gateway API](https://gateway-api.sigs.k8s.io/) replaces the
Ingress resource, implemented by Cilium for both load balancing and TLS
termination. The improvement is the split in ownership: the cluster owns the
`Gateway` — addresses, certificates, ports — and each app owns its own
`HTTPRoute`.

## At a glance

| | |
| --- | --- |
| Namespace | `gateway-system` for the Application, `kube-system` for the Gateways themselves |
| Depends on | [Cilium](cilium.md) to implement it, [cert-manager](cert-manager.md) for the wildcard certificates |
| If it is down | Nothing reaches any hostname. Running pods keep running |
| Health check | `kubectl -n kube-system get gateway` &rarr; both `PROGRAMMED=True` with an address |
| Files | `payload/platform/gateway-api/`, `payload/platform/gateway-api-crds/` |

## Configuration

Two Gateways in `kube-system`, each with a dedicated address from the Cilium
L2 pool, defined in `gateways.yaml`:

| Gateway | Hostname pattern | Used for |
| --- | --- | --- |
| `apps-gateway` | `*.k8s.wlkr.ch` | User-facing workloads, published under the router's public address |
| `infra-gateway` | `*.infra.k8s.wlkr.ch` | Platform services (Grafana, Hubble, etc.), LAN only |

Both terminate TLS with cert-manager's wildcard certificates, so a new
hostname needs no certificate of its own and nothing to renew. Port 80 is
accepted from all namespaces only so the central rule in `http-redirect.yaml`
can send it to HTTPS. `apps-gateway` carries the annotation that makes
[external-dns](external-dns.md#configuration) publish the router's public
address instead of the Gateway's LAN one; the router forwards ports 80 and
443 to that LAN address.

## Usage

Create an `HTTPRoute` in the app's namespace referencing the right Gateway;
[external-dns](external-dns.md) creates the record and the wildcard covers the
name:

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
      sectionName: https
  hostnames:
    - "my-app.k8s.wlkr.ch"
  rules:
    - backendRefs:
        - name: my-app-svc
          port: 80
```

Use `infra-gateway` with a `*.infra.k8s.wlkr.ch` hostname for platform tools.

## Health check

Check that each HTTP listener carries only the redirect — `http` should show
exactly `1`:

```bash
kubectl get gateway infra-gateway -n kube-system \
  -o jsonpath='{range .status.listeners[*]}{.name}={.attachedRoutes}{"\n"}{end}'
```

## Pitfalls

!!! danger "Always set `sectionName: https`"
    A route that names no listener attaches to both, and on port 80 it beats the redirect: Gateway API resolves competing routes by hostname specificity, so a route naming a hostname wins over the catch-all redirect and serves the app in cleartext. Nothing reports it — every route is `Accepted`.

!!! warning "Nothing stops two apps claiming the same hostname"
    Both Gateways admit routes from every namespace, so a stray `HTTPRoute` can attach itself to `infra-gateway` and claim a name — see [Security Posture](../architecture/security.md).
