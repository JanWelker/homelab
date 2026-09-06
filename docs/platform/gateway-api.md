---
description: "Gateway API resources: the apps and infra Gateways, HTTP to HTTPS redirection, and how to expose a new service."
---

# Gateway API

The [Kubernetes Gateway API](https://gateway-api.sigs.k8s.io/) replaces the traditional Ingress resource. It is implemented by Cilium, which handles both load balancing and TLS termination.

The practical improvement over Ingress is the split in ownership: the cluster owns the `Gateway` — addresses, certificates, ports — and each app owns its own `HTTPRoute`. No more twelve-annotation Ingress manifests that only work on the controller they were written for.

## Gateways

Two Gateways are defined in `kube-system`, each with a dedicated IP from the Cilium L2 pool:

| Gateway | IP | Hostname pattern | Used for |
| --- | --- | --- | --- |
| `apps-gateway` | `10.9.2.249` | `*.k8s.wlkr.ch` | User-facing workloads |
| `infra-gateway` | `10.9.2.248` | `*.infra.k8s.wlkr.ch` | Platform services (Grafana, Hubble, etc.) |

Both gateways terminate TLS using wildcard certificates managed by cert-manager. HTTP traffic is accepted on port 80 from all namespaces, purely so a central rule can redirect it to HTTPS rather than leaving plain HTTP quietly working forever.

Wildcard certificates are the reason adding a hostname costs nothing: a new
`*.k8s.wlkr.ch` name is already covered, so there is no per-app certificate to
issue, no ACME rate limit to think about, and nothing to renew.

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

That is the entire procedure. [external-dns](external-dns.md) notices the
hostname and creates the Route53 record; the wildcard certificate already covers
the name. Two things that used to be manual steps, and used to be the two steps
everyone forgot.

!!! warning "Nothing stops two apps claiming the same hostname"
    Both Gateways admit routes from every namespace, so a stray `HTTPRoute` in an unrelated namespace can attach itself to `infra-gateway` and claim a name. Whoever wins is not something you want to determine experimentally — see [Security Posture](../architecture/security.md#authorization).

## Directory Structure

```text
gateway-api/            # Gateway API Resources
├── application.yaml    # ArgoCD Application
├── crds.yaml           # ArgoCD Application for the Gateway API CRDs
├── gateways.yaml       # apps-gateway + infra-gateway
└── http-redirect.yaml  # Central HTTP to HTTPS redirect
```
