---
description: "Pod Security Admission levels per namespace and the first default-deny network policies, with the reasoning for what is enforced and what is only audited."
---

# Security Policies

Four cluster-wide controls: Pod Security Admission, default-deny network
policy in both directions, no API token on the `default` ServiceAccount, and
admission policies on image references, namespace labels and the `authentik`
namespace. Each is easy to turn on and hard to
turn on safely, so the pattern is the same for all of them: measure first,
enforce second, one namespace at a time.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` for the Application; it owns Namespace objects across the cluster |
| Depends on | [Cilium](cilium.md) to enforce the network policies |
| If it is down | Nothing at the time; the labels and policies stay applied and only stop being corrected |
| Health check | `kubectl get ns -L pod-security.kubernetes.io/enforce`, `kubectl get validatingadmissionpolicy` |
| Pruning | Enabled, except for the Namespaces: each carries `argocd.argoproj.io/sync-options: Prune=false`, because pruning a Namespace deletes everything inside it, PVCs included. Anything else removed from Git is pruned, so the Application does not sit OutOfSync on leftovers |
| Files | `payload/platform/security/`, the network policies one file per namespace under `network-policies/`, plus the CoreDNS PodDisruptionBudget — see [Components](index.md#components) |

## Configuration

### Pod Security Admission

A namespace without labels gets the cluster default from
`ansible/templates/admission.yaml.j2`: `enforce` at `baseline`, `audit` and
`warn` at `restricted`. It reaches a control-plane node only at provisioning;
see [Node hardening](../architecture/security.md#node-hardening). It exempts
`kube-system`, which Cilium needs before Argo CD can label anything, and the
namespaces that need `privileged` but carry no labels. An exemption skips
`audit` and `warn` as well, so the labelled namespaces below are not exempt.
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
| `trivy-system` | `privileged` | node-collector hostPath-mounts the kubelet, etcd and CNI directories for the CIS node checks |
| `backup` | `privileged` | The etcd snapshot and defrag jobs use host networking and the etcd certificates; Velero's node-agent mounts the kubelet's pod directory |
| `logging` | `privileged` | Alloy hostPath-mounts the pod logs and the Hubble export |
| `kured` | `privileged` | kured runs privileged in the host PID namespace to reboot the node |
| `cert-manager`, `external-secrets`, `argocd` | `baseline` | — |
| `authentik`, `descheduler`, `external-dns`, `kubelet-csr-approver`, `openbao`, `cilium-secrets`, `default`, `kube-public`, `kube-node-lease` | `restricted` | — |

`privileged` here means "not yet reduced", not "unexamined": `audit` and `warn`
are still `baseline` or `restricted` on all of them. Namespace objects are
owned by this Application so the labels stay declarative rather than drifting
after `CreateNamespace=true`. `cilium-secrets` is the exception: the Cilium
chart creates it, so its labels are `secretsNamespaceLabels` in
`payload/platform/cilium/values.yaml`, and a second owner would fight the
first. Workload namespaces label themselves in the workloads repository, and
`namespace-privileged-allowed` keeps them off `privileged`.

### Network policies

One file per namespace in `network-policies/`, holding that namespace's
`CiliumNetworkPolicy` objects. A namespace gets two whole-namespace policies,
`default-ingress` and `default-egress`, so every pod in it is default-deny in
both directions, plus one policy per workload that needs more than the rest
of the namespace does. Each policy's `description` is the record of who may
connect and why; the rules are not repeated here. `cluster-wide.yaml` holds
the `CiliumClusterwideNetworkPolicy` objects: the rules every pod gets, so a
namespace file is the record of what its namespace may do *beyond* those.

#### Why CiliumNetworkPolicy and not NetworkPolicy

Two kinds of traffic have no pod identity a standard `NetworkPolicy` can name:
Gateway traffic, which reaches a backend from the per-node Envoy, and kubelet
health probes, which come from the node. A plain default-deny therefore kills
ingress *and* probes, and the pods restart forever in a way that looks like an
application fault. Cilium's `fromEntities` names them: `ingress` for
Envoy-proxied traffic, `host` and `remote-node` for the kubelet.

#### Cluster-wide rules

A rule that is word-for-word the same in every namespace file is lifted
into `cluster-wide.yaml`, selecting every endpoint that has a namespace and
with `enableDefaultDeny` off in both directions. The selector is not `{}`
because that also takes the Gateway's `reserved:ingress` endpoint and the
health endpoint, which then carry a policy they have no use for. Default
deny is off because a cluster-wide egress rule would otherwise put every
pod in a namespace without policies into egress default-deny. DNS is the
only rule lifted; the intra-namespace and node-probe rules would qualify
but change what a namespace file says, so they stay.

The second policy there, `init`, is for the seconds before the agent has
resolved a new pod's labels. The pod then carries the reserved `init`
identity, which no namespace policy selects, and the audit log shows its
traffic denied all the same; a scan Job's first lookups and a starting
pod's probes were the bulk of the audit log. It admits the node, DNS out and DNS in, nothing
else.

The third, `cnpg-backups`, selects every CloudNativePG pod by its
`cnpg.io/cluster` label and admits only the object store, by the same two
rules Velero's namespace has. Every database's backup sidecar needs exactly
that, wherever the database runs, so one policy replaces a rule in each
workload's file.

#### What every policy contains

| Rule | Why |
| --- | --- |
| Ingress from within the namespace | Replicas, sidecars and a workload's own database talk to each other on ports that change with the chart |
| Ingress from `host` and `remote-node` | Kubelet probes come from the node, and so do the API server's webhook calls, the aggregation layer and `kubectl port-forward`; all of them carry the node's identity, on a control-plane node with the `kube-apiserver` label as well |
| Ingress from `ingress` | Only where an HTTPRoute sends the Gateway's Envoy straight at the namespace; a namespace behind the Authentik outpost admits `authentik` instead. Kept at L4: the HTTPRoute is already the L7 filter for that traffic |
| Ingress from `monitoring` on the scraped port, `GET /metrics` | The one port a ServiceMonitor or PodMonitor names, at L7, so the scraper can open nothing else |
| Egress within the namespace, and to `kube-apiserver` where there is a Kubernetes client | The `kube-apiserver` entity is the endpoints behind `kubernetes.default`; no pod uses the kube-vip address. DNS is not repeated: the cluster-wide rule above sends every lookup through the proxy, which is what makes `toFQDNs` work and every lookup visible in Hubble |
| Egress to external names as `toFQDNs` | A name reads as the dependency it is; an address does not. The Gateway's own addresses count as external: a pod calling `auth.k8s.wlkr.ch` is classified `world`, not `ingress` |
| Egress to the backend pod as well, when the name is one the Gateway serves | The Gateway's Envoy checks the caller's egress policy a second time, against the backend pod it picked and that pod's port, and answers `403 Access denied` itself when no rule matches; audit mode never sees it. The OIDC clients of Authentik carry both rules |
| HTTP rules on plaintext ports, ingress side only | A request crossing a namespace boundary is proxied once, by the receiving node. TLS and gRPC ports stay at L4; the proxy cannot read them. A machine caller gets the methods and paths a week of Hubble records showed; a browser UI behind the Authentik outpost stays at `http: [{}]`, its paths being the application's own. The path is matched with its query string: a rule anchored with `$` names the query as well, as OpenBao's `/v1/sys/metrics\?format=prometheus$` does |

Host-networked pods (Cilium, kube-vip, the control plane, node-exporter,
Tetragon, the CSI node plugins) have no endpoint of their own and cannot be
selected; to everything else they are `host` or `remote-node`. `kube-system`
therefore covers only its pod-networked workloads, each selected by label.

Where a chart ships `NetworkPolicy` objects of its own (`argocd`) they are
switched off in its values: Cilium unions the two kinds, so a chart rule
that allows everything would silently override the default-deny.

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

### Agent RBAC

`claude-agent-rbac.yaml` gives the in-cluster Claude Code agents two
ClusterRoles for the `claude-reader` and `claude-writer` ServiceAccounts of the
`claude-agents` namespace. Cluster-scoped RBAC cannot come from the `apps`
project, and what an agent may touch is a platform decision.

| Role | Bound | Grants | Leaves out |
| --- | --- | --- | --- |
| `claude-agent-read` | Cluster-wide, to both ServiceAccounts | `get`, `list`, `watch` on named core, workload and CRD kinds, `pods/log` included | Secrets, RBAC, webhooks, CRDs, `pods/exec`, Trivy's exposed-secret reports |
| `claude-agent-write` | Nowhere by the platform: the workloads repository adds a RoleBinding in each namespace a writer session may change | Create, update, patch and delete on pods, workloads, Services, ConfigMaps, jobs, autoscalers and disruption budgets | The above, plus PersistentVolumeClaims (deleting one deletes data), NetworkPolicies and routes (they change who may reach what, which is a pull request) |

- **Creating a pod reads every Secret in its namespace.** That is why the
  write role is bound per namespace, and the `apps` project cannot place a
  RoleBinding in the namespaces its [destinations](argocd.md#projects)
  exclude.
- **`claude-writer-pod-limits`** in `admission-policies.yaml` refuses any pod
  or pod template from the writer that uses the host's network, PID or IPC
  namespace, a `hostPath` volume, a privileged container, or a ServiceAccount
  other than `default` or the one the workload already runs as. A namespace
  without a Pod Security level allows all of those, and another
  ServiceAccount's token is that account's RBAC.
- **`pods/log` is cluster-wide**, so the reader sees whatever any component
  logs, platform namespaces included. Triage needs it, and a cluster-wide grant
  cannot leave out namespaces.

Kinds are listed, never wildcarded, so a new CRD is invisible to the agents
until someone adds it. Argo CD reverts a hand edit within minutes either way.

### Admission policies

Trivy reports a bad image after it is running; `admission-policies.yaml`
refuses or records it at admission, with the built-in
`ValidatingAdmissionPolicy` rather than a policy engine. The image policies
match Pods and every controller that produces them, so a bad Deployment is
rejected at `kubectl apply` rather than failing quietly in its ReplicaSet. The
other three close what RBAC and the `apps` project cannot express.

| Policy | Action | Why |
| --- | --- | --- |
| `image-tag-pinned` | `Deny` | A tag other than `latest`, or a digest. Nothing running violates it, and an unpinned image is the one Renovate can neither track nor roll back |
| `image-registry-allowed` | `Audit` and `Warn` | The registries the cluster pulls from today; a reference with no registry counts as `docker.io`. Recorded to the [audit log](../architecture/audit-logging.md) and shown to whoever applies, not enforced, until the record is clean |
| `claude-writer-pod-limits` | `Deny` | Host access and borrowed ServiceAccounts from the agent writer, see [Agent RBAC](#agent-rbac) |
| `namespace-privileged-allowed` | `Deny` | A Namespace may carry `enforce: privileged` only if it is in the policy's list, the `privileged` rows above. The `apps` project creates its own Namespaces and their labels, so without it a workload could opt out of Pod Security |
| `authentik-foreign-objects` | `Deny` | In `authentik`, an object an Argo CD Application applied must come from `authentik` or `security`, or be the ConfigMap `authentik-blueprint-<application>`. Argo CD cannot whitelist kinds per destination namespace, so this is what holds `apps` to its blueprint there; objects with no `argocd.argoproj.io/tracking-id` annotation, from controllers and `kubectl`, are not matched |

All but one carry `failurePolicy: Ignore`: an expression that errors lets the
request through rather than stopping every pod in the cluster. A real violation
is still refused. `claude-writer-pod-limits` fails closed, since it only ever
matches one identity.

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
are not enforced either.

1. Merge the values change; the agents roll on their own. `Enabled` on
   every node before anything else merges:

    ```bash
    kubectl -n kube-system rollout status ds/cilium
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
   `policyAuditMode: false`, which rolls the agents again. The first
   week's reading is [Network Policy Audit 2026-09-27](../operations/network-policy-audit-2026-09-27.md), the reading before the flip [Network Policy Audit 2026-10-16](../operations/network-policy-audit-2026-10-16.md). From then on the
   verdict to watch is `DROPPED`, live or from Loki — see
   [Cilium](cilium.md#health-check) — and the
   [`HubblePolicyDrops`](logging.md#alerting) alert mails each refused
   connection.

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
kubectl get ccnp
kubectl get validatingadmissionpolicy
```

## Pitfalls

!!! note "Audit mode stops at the proxy"
    `policyAuditMode` is a datapath setting. A request the Gateway's Envoy or an HTTP rule refuses is answered `403 Access denied` on the spot, and Hubble records it as a forwarded response, not an audited verdict. Look for it with `hubble observe --http-status 403`; in Loki the denied request is a `DROPPED` flow of type `REQUEST` whose source carries the caller's namespace and the `ingress` identity. The agents' `argocd` calls failed this way, `server: envoy` and `grpc-message: Access denied`, while their policy named the host but not the `argocd-server` pod; see [Agent account](argocd.md#agent-account).

!!! note "A name rule audits one SYN in twenty"
    A `toFQDNs` rule admits the addresses the DNS proxy has seen for the name, and the proxy releases the answer after at most 100ms whether or not every datapath has the new address. About one connection in twenty to a CDN name is recorded `AUDIT` with a `world` destination; under enforcement that `SYN` drops and the retransmit a second later goes through. A run of drops on one source port is different: the application connected to an address it cached past the DNS TTL.

!!! note "The token change is not retroactive"
    The mount is decided at admission, so existing pods keep their token until recreated. That makes the change safe to roll out, and means a posture scan will not agree it is fixed until things restart.
