---
description: "Pod Security Admission levels per namespace and the first default-deny network policies, with the reasoning for what is enforced and what is only audited."
---

# Security Policies

Four cluster-wide controls: Pod Security Admission, default-deny network
policy in both directions, no API token on the `default` ServiceAccount, and
two admission policies on image references. Each is easy to turn on and hard to
turn on safely, so the pattern is the same for all of them: measure first,
enforce second, one namespace at a time.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` for the Application; it owns Namespace objects across the cluster |
| Depends on | [Cilium](cilium.md) to enforce the network policies |
| If it is down | Nothing at the time; the labels and policies stay applied and only stop being corrected |
| Health check | `kubectl get ns -L pod-security.kubernetes.io/enforce`, `kubectl get validatingadmissionpolicy` |
| Pruning | **Disabled.** Pruning a Namespace deletes everything inside it, PVCs included |
| Files | `payload/platform/security/`, the network policies one file per namespace under `network-policies/` |

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
| `trivy-system` | `privileged` | node-collector hostPath-mounts the kubelet, etcd and CNI directories for the CIS node checks |
| `cert-manager`, `external-secrets`, `argocd` | `baseline` | — |

`privileged` here means "not yet reduced", not "unexamined": `audit` and `warn`
are still `baseline` or `restricted` on all of them. Namespace objects are
owned by this Application so the labels stay declarative rather than drifting
after `CreateNamespace=true`.

### Network policies

One file per namespace in `network-policies/`, holding that namespace's
`CiliumNetworkPolicy` objects. A namespace gets two whole-namespace policies,
`default-ingress` and `default-egress`, so every pod in it is default-deny in
both directions, plus one policy per workload that needs more than the rest
of the namespace does. Each policy's `description` is the record of who may
connect and why; the rules are not repeated here.

#### Why CiliumNetworkPolicy and not NetworkPolicy

Two kinds of traffic have no pod identity a standard `NetworkPolicy` can name:
Gateway traffic, which reaches a backend from the per-node Envoy, and kubelet
health probes, which come from the node. A plain default-deny therefore kills
ingress *and* probes, and the pods restart forever in a way that looks like an
application fault. Cilium's `fromEntities` names them: `ingress` for
Envoy-proxied traffic, `host` and `remote-node` for the kubelet.

#### What every policy contains

| Rule | Why |
| --- | --- |
| Ingress from within the namespace | Replicas, sidecars and a workload's own database talk to each other on ports that change with the chart |
| Ingress from `host` and `remote-node` | Kubelet probes come from the node, and so do the API server's webhook calls, the aggregation layer and `kubectl port-forward`; all of them carry the node's identity, on a control-plane node with the `kube-apiserver` label as well |
| Ingress from `ingress` | Only where an HTTPRoute sends the Gateway's Envoy straight at the namespace; a namespace behind the Authentik outpost admits `authentik` instead. Kept at L4: the HTTPRoute is already the L7 filter for that traffic |
| Ingress from `monitoring` on the scraped port, `GET /metrics` | The one port a ServiceMonitor or PodMonitor names, at L7, so the scraper can open nothing else |
| Egress within the namespace, to kube-dns with a DNS rule, and to `kube-apiserver` where there is a Kubernetes client | `toFQDNs` only works when the DNS proxy sees the answers; `matchPattern: "*"` refuses nothing and makes every lookup visible in Hubble. The `kube-apiserver` entity is the endpoints behind `kubernetes.default`; no pod uses the kube-vip address |
| Egress to external names as `toFQDNs` | A name reads as the dependency it is; an address does not. The Gateway's own addresses count as external: a pod calling `auth.k8s.wlkr.ch` is classified `world`, not `ingress` |
| HTTP rules on plaintext ports, ingress side only | A request crossing a namespace boundary is proxied once, by the receiving node. TLS and gRPC ports stay at L4; the proxy cannot read them |

Host-networked pods (Cilium, kube-vip, the control plane, node-exporter,
Tetragon, the CSI node plugins) have no endpoint of their own and cannot be
selected; to everything else they are `host` or `remote-node`. `kube-system`
therefore covers only its pod-networked workloads, each selected by label.

Where a chart ships `NetworkPolicy` objects of its own (`argocd`,
`authentik`) they are switched off in its values: Cilium unions the two
kinds, so a chart rule that allows everything would silently override the
default-deny.

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

### Admission policies

Trivy reports a bad image after it is running; `admission-policies.yaml`
refuses or records it at admission, with the built-in
`ValidatingAdmissionPolicy` rather than a policy engine. Both match Pods and
every controller that produces them, so a bad Deployment is rejected at
`kubectl apply` rather than failing quietly in its ReplicaSet.

| Policy | Action | Why |
| --- | --- | --- |
| `image-tag-pinned` | `Deny` | A tag other than `latest`, or a digest. Nothing running violates it, and an unpinned image is the one Renovate can neither track nor roll back |
| `image-registry-allowed` | `Audit` and `Warn` | The registries the cluster pulls from today; a reference with no registry counts as `docker.io`. Recorded to the [audit log](../architecture/audit-logging.md) and shown to whoever applies, not enforced, until the record is clean |

Both carry `failurePolicy: Ignore`: an expression that errors lets the request
through rather than stopping every pod in the cluster. A real violation is
still refused.

## Usage

### Promoting the registry allowlist

1. Confirm nothing running would be refused, over the full Loki retention:

    ```logql
    {job="kubernetes-audit"} |= "validation.policy.admission.k8s.io/validation_failure" |= "image-registry-allowed"
    ```

2. Change `validationActions` on the binding in `admission-policies.yaml` to
   `["Deny"]`.

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

### Rollout

A new policy is not enforced on arrival. `policyAuditMode` in
`payload/platform/cilium/values.yaml` makes every agent evaluate each verdict
and log it with `AUDIT` instead of dropping, and those verdicts reach Loki the
way drops do. The mode is cluster-wide, so while it is on the older policies
are not enforced either, and the agent reads it at startup.

1. Merge the values change and restart the agents; `Enabled` on every node
   before anything else merges:

    ```bash
    kubectl -n kube-system rollout restart ds/cilium
    kubectl -n kube-system exec ds/cilium -c cilium-agent -- cilium-dbg config get PolicyAuditMode
    ```

2. Merge the policies and let them run for a week. What enforcement would have
   refused, by namespace pair:

    ```promql
    sum by (source_namespace, destination_namespace) (increase(hubble_policy_verdicts_total{action="audit"}[7d]))
    ```

    and flow by flow, in Grafana:

    ```logql
    {job="hubble", verdict="AUDIT"} | json | line_format "{{.flow_source_namespace}}/{{.flow_source_pod_name}} -> {{.flow_destination_namespace}}/{{.flow_destination_pod_name}} {{.flow_l4_TCP_destination_port}}{{.flow_l4_UDP_destination_port}}"
    ```

3. Add a rule for every audited flow that is legitimate. Layer 7 rules are
   never audited: a request an HTTP rule does not match is answered `403` on
   the spot, which is why a new policy carries `http: [{}]` — proxied and
   recorded, nothing refused — until the week's requests have been read:

    ```logql
    {job="hubble"} | json | flow_l7_http_method != "" | line_format "{{.flow_source_namespace}} -> {{.flow_destination_namespace}}:{{.flow_l4_TCP_destination_port}} {{.flow_l7_http_method}} {{.flow_l7_http_url}}"
    ```

4. Tighten each `http: [{}]` to the methods and paths seen, set
   `policyAuditMode: false` and restart the agents again. From then on the
   verdict to watch is `DROPPED`, live or from Loki — see
   [Cilium](cilium.md#health-check) — and the `HubblePolicyDrops` alert
   fires on a sustained one.

If a single endpoint needs the same treatment later, audit mode can be set on
it alone, until the agent restarts:

```bash
kubectl -n kube-system exec ds/cilium -c cilium-agent -- \
  cilium-dbg endpoint config <endpoint-id> PolicyAuditMode=Enabled
```

## Health check

```bash
kubectl get ns -L pod-security.kubernetes.io/enforce
kubectl get cnp -A
kubectl get validatingadmissionpolicy
```

## Pitfalls

!!! note "The token change is not retroactive"
    The mount is decided at admission, so existing pods keep their token until recreated. That makes the change safe to roll out, and means a posture scan will not agree it is fixed until things restart.
