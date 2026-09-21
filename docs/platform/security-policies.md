---
description: "Pod Security Admission levels per namespace and the first default-deny network policies, with the reasoning for what is enforced and what is only audited."
---

# Security Policies

Three cluster-wide controls: Pod Security Admission, default-deny ingress
network policy, and no API token on the `default` ServiceAccount. Each is
easy to turn on and hard to turn on safely, so the pattern is the same for
all three: measure first, enforce second, one namespace at a time.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` for the Application; it owns Namespace objects across the cluster |
| Stage | `11-policy`, last, so it labels namespaces that already exist |
| Depends on | [Cilium](cilium.md) to enforce the network policies |
| If it is down | Nothing at the time; the labels and policies stay applied and only stop being corrected |
| Health check | `kubectl get ns -L pod-security.kubernetes.io/enforce` |
| Pruning | **Disabled.** Pruning a Namespace deletes everything inside it, PVCs included |
| Files | `payload/platform/security/` |

## Configuration

### Pod Security Admission

A namespace that does not opt in runs at `privileged`, which enforces nothing.
Each namespace in `pod-security.yaml` carries three labels:

| Label | Set to | Effect |
| --- | --- | --- |
| `enforce` | The level the namespace demonstrably needs | Rejects pods that violate it |
| `audit` | Stricter | Records violations in the [API server audit log](../architecture/audit-logging.md) |
| `warn` | Stricter | Warns whoever applies the manifest |

Enforcement is set to what already works, so nothing running breaks, while
`warn` and `audit` show what a stricter level would catch; tightening later is
an informed change.

| Namespace | Enforce | Why not stricter |
| --- | --- | --- |
| `kube-system` | `privileged` | Cilium, kube-vip and the control plane use host networking and host paths |
| `rook-ceph` | `privileged` | OSDs need raw block devices |
| `monitoring` | `privileged` | node-exporter is host-networked and reads `/proc` and `/sys` |
| `openbao` | `privileged` | Adds `IPC_LOCK` to keep the root key out of swap, which baseline does not allow |
| `cert-manager`, `external-secrets`, `argocd` | `baseline` | — |

`privileged` here means "not yet reduced", not "unexamined": `audit` and `warn`
are still `baseline` or `restricted` on all of them. Namespace objects are
owned by this Application so the labels stay declarative rather than drifting
after `CreateNamespace=true`.

### Network policies

Ingress only, in nine namespaces, as `CiliumNetworkPolicy` in
`network-policies.yaml`. Every policy also admits traffic from within the
namespace and from `host` and `remote-node` for probes.

#### Why CiliumNetworkPolicy and not NetworkPolicy

Two kinds of traffic have no pod identity a standard `NetworkPolicy` can name:
Gateway traffic, which reaches a backend from the per-node Envoy, and kubelet
health probes, which come from the node. A plain default-deny therefore kills
ingress *and* probes, and the pods restart forever in a way that looks like an
application fault. Cilium's `fromEntities` names them: `ingress` for
Envoy-proxied traffic, `host` and `remote-node` for the kubelet.

#### Scope

| Namespace | Who may connect from outside it |
| --- | --- |
| `openbao` | The Gateway (UI), Prometheus and ESO, on `8200` |
| `cert-manager` | Prometheus; the webhook is called by the API server from the node |
| `external-secrets` | Prometheus; the webhook is called by the API server from the node |
| `monitoring` | The Gateway (Grafana); kured on `9090`; the Authentik outpost on `9090` and `9093`; the Ceph mgr on `9090` and `3000` |
| `external-dns` | Prometheus |
| `kubelet-csr-approver` | Prometheus |
| `kured` | Prometheus |
| `logging` | Prometheus and Grafana, both in `monitoring` |
| `cnpg-system` | Prometheus; the webhooks are called by the API server from the node |

The `monitoring` callers are easy to lose: Kured blocks every reboot when its
Prometheus query fails, the Authentik outpost is what `prometheus.infra` and
`alertmanager.infra` resolve to, and the Ceph dashboard pulls from both.
`cnpg-system` covers the operator only; each database lives in its workload's
namespace, so the rule admitting the operator on port `8000` belongs to that
namespace's policy — see [Adding a Workload](../development/add-workload.md).

Egress is untouched: a default-deny there also needs DNS, the API server and
every external endpoint, and getting it wrong takes the component down rather
than leaving it exposed. Not covered, each for its own reason:

| Namespace | Why not yet |
| --- | --- |
| `kube-system` | Holds Cilium itself, the static control-plane pods and kube-vip |
| `rook-ceph` | Mons, OSDs and CSI plugins have a wide, partly host-level traffic matrix; Ceph health is the verification signal |
| `backup` | A node-agent doing volume backups through the CSI plugins, and a host-network CronJob reading etcd |
| `argocd`, `authentik` | Already carry `NetworkPolicy` objects from their own charts |

### Default ServiceAccount tokens

A pod that names no `serviceAccountName` lands on `default`, which nothing
configures, and gets an API token it has no use for. A workload reaching the
API through `default` is already a misconfiguration, so
`default-serviceaccounts.yaml` sets `automountServiceAccountToken: false` on it
in every namespace with nothing to preserve, empty namespaces included. Left
out: `kube-system`, where the control plane authenticates with client
certificates and "very likely fine" is not the standard, and `authentik`,
where `authentik-server` sets no `serviceAccountName` — an upstream chart
default, to be fixed at the source.

## Usage

### Tightening a namespace

1. See what running pods a stricter level would reject, without changing
   anything:

    ```bash
    kubectl label --dry-run=server --overwrite ns cert-manager \
      pod-security.kubernetes.io/enforce=restricted
    ```

2. Catch what is not running right now — a nightly CronJob, say — from the
   `audit` label's records over the full Loki retention window, in Grafana:

    ```logql
    {job="kubernetes-audit"} |= "pod-security.kubernetes.io/audit-violations"
    ```

3. Raise `enforce` in `pod-security.yaml`.

### Rolling out a network policy

One namespace at a time, watching Hubble between each:

```bash
kubectl -n kube-system port-forward svc/hubble-relay 4245:80
hubble observe --verdict DROPPED --namespace openbao --follow
```

If something legitimate is dropped, put a single endpoint into audit mode —
decisions logged, not enforced — to find the missing rule without an outage:

```bash
kubectl -n kube-system exec ds/cilium -- \
  cilium endpoint config <endpoint-id> PolicyAuditMode=Enabled
```

## Health check

```bash
kubectl get ns -L pod-security.kubernetes.io/enforce
kubectl get cnp -A
```

## Pitfalls

!!! note "The token change is not retroactive"
    The mount is decided at admission, so existing pods keep their token until recreated. That makes the change safe to roll out, and means a posture scan will not agree it is fixed until things restart.
