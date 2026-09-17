---
description: "Pod Security Admission levels per namespace and the first default-deny network policies, with the reasoning for what is enforced and what is only audited."
---

# Security Policies

Three cluster-wide controls: Pod Security Admission, default-deny network
policy, and no API token on the `default` ServiceAccount.

Both are the kind of thing that is easy to turn on and hard to turn on *safely*.
The pattern used here — measure first, enforce second, one namespace at a time —
is unglamorous and is the only approach that survives contact with a running
cluster.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` for the Application; it owns Namespace objects across the cluster |
| Stage | `11-policy`, last, so it labels namespaces that already exist |
| Depends on | [Cilium](cilium.md) to enforce the network policies |
| If it is down | Nothing at the time. The labels and policies are already applied; what stops is them being corrected if something changes them |
| Health check | `kubectl get ns -L pod-security.kubernetes.io/enforce` |
| Pruning | **Disabled.** This Application owns Namespaces, and pruning one deletes everything inside it |

## Pod Security Admission

PSA is the built-in replacement for PodSecurityPolicy, and a namespace that does
not opt in runs at the default `privileged` level — which enforces nothing at
all. Every namespace you have not thought about is wide open, silently, by
default. That is the fact this section exists to address.

Each namespace carries three labels, and the split between them is the point:

| Label | Set to | Effect |
| --- | --- | --- |
| `enforce` | The level the namespace demonstrably needs | Rejects pods that violate it |
| `audit` | Stricter | Records violations in the [API server audit log](../architecture/audit-logging.md) |
| `warn` | Stricter | Warns whoever applies the manifest |

Enforcement is set to what already works, so **nothing running breaks**, while
`warn` and `audit` show what a stricter level would have caught. Tightening
enforcement later becomes an informed change rather than a guess — and a
security control that broke production once is a security control that gets
switched off permanently, which is the real risk here.

| Namespace | Enforce | Why not stricter |
| --- | --- | --- |
| `kube-system` | `privileged` | Cilium, kube-vip and the control plane all use host networking and host paths |
| `rook-ceph` | `privileged` | OSDs need raw block devices |
| `monitoring` | `privileged` | node-exporter is host-networked and reads `/proc` and `/sys` |
| `openbao` | `privileged` | Adds `IPC_LOCK` to keep the root key out of swap — not on baseline's capability allow-list |
| `kubescape` | `privileged` | The eBPF node-agent needs `hostPID`, hostPath mounts of `/` and `/sys`, and seven added capabilities — see [Kubescape &rarr; Namespace security level](kubescape.md#namespace-security-level) |
| `cert-manager` | `baseline` | — |
| `external-secrets` | `baseline` | — |
| `argocd` | `baseline` | — |

!!! note "`privileged` here means 'not yet reduced', not 'unexamined'"
    Each of the five has a specific reason above. `audit` and `warn` are still set to `baseline` or `restricted` on all of them, so the violations are visible even where they are not blocked. The distinction matters when you come back in a year: a documented exception is a decision, an undocumented one is just something nobody got around to.

Namespace objects are owned by this Application so the labels stay declarative
rather than being applied once by `CreateNamespace=true` and then drifting.
**Pruning is disabled** for the whole Application — pruning a `Namespace` deletes
everything inside it, including PVCs, so a misplaced deletion in Git becomes an
irreversible data loss event roughly three minutes later. That is not a mistake
worth leaving available to a sleepy Tuesday.

### Checking what would break

```bash
kubectl label --dry-run=server --overwrite ns cert-manager \
  pod-security.kubernetes.io/enforce=restricted
```

That reports every pod in the namespace that would be rejected, without
changing anything.

It only sees what is running *right now*, though — a CronJob that fires nightly
and violates `restricted` is invisible to a dry run at three in the afternoon.
The `audit` label covers that gap by recording violations continuously, and
those records are queryable in Grafana over the full Loki retention window:

```logql
{job="kubernetes-audit"} |= "pod-security.kubernetes.io/audit-violations"
```

Use the dry run to check the common case and the audit log to catch the rest.
Between them, tightening `enforce` stops being a guess.

## Network policies

Without a policy, pod-to-pod traffic is unrestricted across all namespaces:
anything with a foothold in one pod can reach OpenBao's API, the Ceph mons and
the Kubernetes API alike. The flat network is Kubernetes' default and it is
almost never what anyone actually wants — it is just what you get for free.

### Why CiliumNetworkPolicy and not NetworkPolicy

This is the detail that makes or breaks a default-deny here. Two kinds of
traffic have no pod identity a standard `NetworkPolicy` can name:

- **Gateway traffic.** With Cilium's Gateway API implementation, a request
  reaching a backend arrives from the per-node Envoy, not from a pod.
- **Health probes.** kubelet's liveness and readiness probes come from the node
  itself.

A plain `NetworkPolicy` default-deny therefore kills all ingress *and* all
health probes — the pods then fail their probes and restart forever, which looks
exactly like an application fault and not at all like a policy one. Expect to
spend a while reading application logs before it occurs to you that the
application is fine and the kubelet is the one being blocked. Cilium's
`fromEntities` names these directly: `ingress` for Envoy-proxied traffic,
`host` and `remote-node` for the kubelet.

### Scope

Ingress only, in eight namespaces:

| Namespace | Who may connect from outside it |
| --- | --- |
| `openbao` | The Gateway (UI), Prometheus and ESO, on `8200` |
| `cert-manager` | Prometheus; the webhook is called by the API server from the node |
| `external-secrets` | Prometheus; the webhook is called by the API server from the node |
| `monitoring` | The Gateway (Grafana), and kured on `9090` |
| `external-dns` | Prometheus |
| `kubelet-csr-approver` | Prometheus |
| `kured` | Prometheus |
| `logging` | Prometheus and Grafana, both in `monitoring` |

Every policy also admits traffic from within the namespace and from `host` and
`remote-node` for probes. The `monitoring` rule for kured is easy to miss and
costly to lose: kured queries Prometheus before every reboot and blocks when the
query fails, so without it no node ever reboots.

Egress is deliberately untouched. A default-deny on egress also needs rules for
DNS, the API server, and every external endpoint each component talks to;
getting that wrong takes the component down rather than merely leaving it
exposed. And you will get it wrong at least twice, because nobody has a complete
list of what their components phone home to. It is the next step to take, not
one that is taken here.

The rest are **not** covered and allow all ingress, each for its own reason:

| Namespace | Why not yet |
| --- | --- |
| `kube-system` | Holds Cilium itself, the static control-plane pods and kube-vip; a default-deny there is enforced by the component being restricted |
| `rook-ceph` | Mons, OSDs and CSI plugins have a wide, partly host-level traffic matrix, and want Ceph health as the verification signal rather than a guess |
| `backup` | A node-agent doing volume backups through the CSI plugins, and a host-network CronJob reading etcd |
| `kubescape` | The node-agent talks to both the operator and the aggregated storage API server, and getting it wrong disables posture scanning silently |
| `argocd`, `authentik` | Already carry `NetworkPolicy` objects from their own charts |

### Rolling this out safely

One namespace at a time, watching Hubble between each. Rolling out network
policy everywhere at once is how people end up reverting the whole thing at
midnight and never trying again:

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

## Default ServiceAccount tokens

Every pod that does not opt out gets a projected API token mounted at
`/var/run/secrets/kubernetes.io/serviceaccount/token`. For a workload that never
calls the API server, that is a credential handed to a process with no use for
it — and the first thing anything with code execution in the container picks up.

The `default` ServiceAccount is the worst case. Kubernetes creates one in every
namespace, nothing configures it, and a pod that names no `serviceAccountName`
silently lands on it. A workload reaching the API through `default` is already a
misconfiguration, so `default-serviceaccounts.yaml` sets
`automountServiceAccountToken: false` on it everywhere with nothing to preserve.
That includes namespaces with no workloads at all (`default`, `kube-public`,
`kube-node-lease`, `gateway-system`, `cilium-secrets`): a mountable token in an
empty namespace stops being harmless the day something is deployed there.

Two namespaces are left out, because something really runs on their `default`
SA:

- **`kube-system`.** etcd, the API server, controller-manager and scheduler run
  there. They authenticate with client certificates from kubeconfigs on the
  node, so removing the token would very likely be fine — and "very likely
  fine" is not the standard for the control plane.
- **`authentik`.** `authentik-server` sets no `serviceAccountName`, while
  `authentik-worker` beside it uses the `authentik` SA. That asymmetry is an
  upstream chart default, to be fixed at the source rather than worked around.

!!! note "Not retroactive"
    The token mount is decided at admission, so existing pods keep theirs until
    they are recreated. That makes the change safe to roll out, and also means a
    Kubescape scan will not agree it is fixed until things restart.

## Directory Structure

```text
security/
├── application.yaml              # ArgoCD Application, prune disabled
├── pod-security.yaml             # Namespace objects with PSA labels
├── network-policies.yaml         # Default-deny ingress, eight namespaces
└── default-serviceaccounts.yaml  # No token automount on `default` SAs
```
