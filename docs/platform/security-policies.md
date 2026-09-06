---
description: "Pod Security Admission levels per namespace and the first default-deny network policies, with the reasoning for what is enforced and what is only audited."
---

# Security Policies

Two cluster-wide controls: Pod Security Admission, and default-deny network
policy.

## Pod Security Admission

PSA is the built-in replacement for PodSecurityPolicy, and a namespace that does
not opt in runs at the default `privileged` level — which enforces nothing.

Each namespace carries three labels, and the split between them is the point:

| Label | Set to | Effect |
| --- | --- | --- |
| `enforce` | The level the namespace demonstrably needs | Rejects pods that violate it |
| `audit` | Stricter | Records violations in the API server audit log |
| `warn` | Stricter | Warns whoever applies the manifest |

Enforcement is set to what already works, so **nothing running breaks**, while
`warn` and `audit` show what a stricter level would have caught. Tightening
enforcement later becomes an informed change rather than a guess.

| Namespace | Enforce | Why not stricter |
| --- | --- | --- |
| `kube-system` | `privileged` | Cilium, the kube-vip static pod and the control plane all use host networking and host paths |
| `rook-ceph` | `privileged` | OSDs need raw block devices |
| `monitoring` | `privileged` | node-exporter is host-networked and reads `/proc` and `/sys` |
| `openbao` | `privileged` | Adds `IPC_LOCK` to keep the root key out of swap — not on baseline's capability allow-list |
| `cert-manager` | `baseline` | — |
| `external-secrets` | `baseline` | — |
| `argocd` | `baseline` | — |

!!! note "`privileged` here means 'not yet reduced', not 'unexamined'"
    Each of the four has a specific reason above. `audit` and `warn` are still
    set to `baseline` or `restricted` on all of them, so the violations are
    visible even where they are not blocked.

Namespace objects are owned by this Application so the labels stay declarative
rather than being applied once by `CreateNamespace=true` and then drifting.
**Pruning is disabled** for the whole Application — pruning a `Namespace`
deletes everything inside it, which is not a mistake worth leaving available.

### Checking what would break

```bash
kubectl label --dry-run=server --overwrite ns cert-manager \
  pod-security.kubernetes.io/enforce=restricted
```

That reports every pod in the namespace that would be rejected, without
changing anything.

## Network policies

Without a policy, pod-to-pod traffic is unrestricted across all namespaces:
anything with a foothold in one pod can reach OpenBao's API, the Ceph mons and
the Kubernetes API alike.

### Why CiliumNetworkPolicy and not NetworkPolicy

This is the detail that makes or breaks a default-deny here. Two kinds of
traffic have no pod identity a standard `NetworkPolicy` can name:

- **Gateway traffic.** With Cilium's Gateway API implementation, a request
  reaching a backend arrives from the per-node Envoy, not from a pod.
- **Health probes.** kubelet's liveness and readiness probes come from the node
  itself.

A plain `NetworkPolicy` default-deny therefore kills all ingress *and* all
health probes — the pods then fail their probes and restart forever, which
looks like an application fault rather than a policy one. Cilium's
`fromEntities` names these directly: `ingress` for Envoy-proxied traffic,
`host` and `remote-node` for the kubelet.

### Scope

Ingress only, in four namespaces: `openbao`, `cert-manager`,
`external-secrets`, `monitoring`.

Egress is deliberately untouched. A default-deny on egress also needs rules for
DNS, the API server, and every external endpoint each component talks to;
getting that wrong takes the component down rather than merely leaving it
exposed. It is the next step to take, not one that is taken here.

The remaining namespaces — `logging`, `backup`, `authentik`, `kured`,
`metrics-server` — are **not** covered and allow all ingress.

### Rolling this out safely

Apply one namespace at a time and watch drops in Hubble between each:

```bash
kubectl -n kube-system port-forward svc/hubble-relay 4245:80
hubble observe --verdict DROPPED --namespace openbao --follow
```

If something legitimate is being dropped, Cilium can put a single endpoint into
audit mode — policy decisions are logged but not enforced — which is the
fastest way to find a missing rule without an outage:

```bash
kubectl -n kube-system exec ds/cilium -- \
  cilium endpoint config <endpoint-id> PolicyAuditMode=Enabled
```

## Directory Structure

```text
security/
├── application.yaml        # ArgoCD Application, prune disabled
├── pod-security.yaml       # Namespace objects with PSA labels
└── network-policies.yaml   # Default-deny ingress, four namespaces
```
