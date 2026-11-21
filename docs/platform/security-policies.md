---
description: "Pod Security Admission levels per namespace and the first default-deny network policies, with the reasoning for what is enforced and what is only audited."
---

# Security Policies

Five cluster-wide controls: Pod Security Admission, default-deny network
policy in both directions, a host firewall on the nodes, no API token on the
`default` ServiceAccount, and admission policies on image references, namespace
labels and the `authentik` namespace. The pattern is the same for all: measure first,
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
only `kube-system`, which Cilium needs before Argo CD can label anything; an
exemption skips `audit` and `warn` as well.
Each namespace in `pod-security.yaml` carries three labels:

| Label | Set to | Effect |
| --- | --- | --- |
| `enforce` | The level the namespace demonstrably needs | Rejects pods that violate it |
| `audit` | Stricter | Records violations in the [API server audit log](../architecture/audit-logging.md) |
| `warn` | Stricter | Warns whoever applies the manifest |

The level per namespace is in `pod-security.yaml`; the rule behind it:

| Enforce | Which namespaces | Why not stricter |
| --- | --- | --- |
| `privileged` | Those with a component on the host: the CNI and control plane, Ceph's OSDs, node-exporter, Trivy's node-collector, the etcd jobs and Velero's node-agent, Alloy, Kured | Host networking, the host PID namespace, raw block devices or a hostPath under the kubelet, etcd or log directories, all of which `baseline` forbids. node-exporter has a namespace of its own (`namespaceOverride`) so the rest of the monitoring stack need not follow it |
| `baseline` | Those with a container that starts as root or escalates: Grafana's `init-chown-data`, the runners and their job pods, and the operators not yet checked against `restricted` | Reduced when the chart offers a non-root path, one namespace at a time |
| `restricted` | Everything else, and the namespaces that run no pods | — |

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
ingress *and* probes, and the pods restart forever. Cilium's `fromEntities` names them: `ingress` for
Envoy-proxied traffic, `host` and `remote-node` for the kubelet.

#### Cluster-wide rules

A rule that is word-for-word the same in every namespace file is lifted
into `cluster-wide.yaml`, selecting every endpoint that has a namespace and
with `enableDefaultDeny` off in both directions. The selector is not `{}`
because that also takes the Gateway's `reserved:ingress` endpoint and the
health endpoint. Default
deny is off there, since a cluster-wide egress rule would otherwise close every
pod of a namespace without policies. DNS is the only rule lifted; the
intra-namespace and node-probe rules stay in the namespace files.

The second policy there, `init`, is for the seconds before the agent has
resolved a new pod's labels: the pod carries the reserved `init` identity,
which no namespace policy selects. It admits the node, DNS out and DNS in, nothing
else.

The third, `cnpg-backups`, selects every CloudNativePG pod by its
`cnpg.io/cluster` label and admits only the object store, by the same two
rules Velero's namespace has. Every database's backup sidecar needs exactly
that, wherever the database runs, so one policy replaces a rule per workload.

The fourth, `default-deny`, closes every pod outside `kube-system` in both
directions except the node and DNS, so a namespace that
ships no `default-*` policies is closed until it allows what it needs;
namespaces with them are unchanged.

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
| HTTP rules on plaintext ports, ingress side only | A request crossing a namespace boundary is proxied once, by the receiving node. TLS and gRPC ports stay at L4; the proxy cannot read them. A machine caller gets the methods and paths Hubble recorded; a browser UI behind the Authentik outpost stays at `http: [{}]`. The path is matched with its query string: a rule anchored with `$` names the query as well, as OpenBao's `/v1/sys/metrics\?format=prometheus$` does |

Host-networked pods (Cilium, kube-vip, the control plane, node-exporter,
Tetragon, the CSI node plugins) have no endpoint of their own and cannot be
selected; to everything else they are `host` or `remote-node`. `kube-system`
therefore covers only its pod-networked workloads, each selected by label.

Where a chart ships `NetworkPolicy` objects of its own (`argocd`) they are
switched off in its values: Cilium unions the two kinds, so a chart rule
that allows everything would silently override the default-deny.

### Host firewall

Pod policies cannot select a host-networked port. A `CiliumClusterwideNetworkPolicy`
with a `nodeSelector` can, and Cilium's
[host firewall](https://docs.cilium.io/en/stable/security/host-firewall/) enforces
it on the native device and `cilium_host`. What each port is for is in
[Host Firewall Inventory 2026-10-30](../operations/host-firewall-inventory-2026-10-30.md).

| Behaviour | Why it matters |
| --- | --- |
| Only packets addressed to a node IP are checked | The kube-vip address counts on the node holding it; Gateway addresses and NodePorts are translated first, so their policy is the backend pod's |
| A node with no selecting policy is not denied anything on IPv4 | The switch can merge alone, and reverting the policy needs no second rollout |
| No rule in a direction leaves that direction unenforced | A host policy with `ingress` only never blocks a node's own connections, and their replies pass through conntrack |
| Host endpoint labels are filtered | `kubernetes.io/hostname` is not among them, so a node can only be chosen by a label such as `node-access` in the upstream example, or by the `control-plane` role label |
| A pod reaching another node's IP is masqueraded to its own node | The target sees `remote-node`, so the firewall can tell the LAN from the cluster but not Prometheus from any other pod; the source pod's egress policy decides that |
| A connection a node opened before its agent started has no conntrack entry | For about half a minute after a boot the firewall drops the replies to it, typically the kubelet's first connection to the control-plane address, which Cilium sees as `world`; the client reconnects and nothing else is lost |

`network-policies/host-firewall.yaml` selects every node without
`host-firewall.wlkr.ch/exempt`, so a new node is filtered from its first boot
and the label is the one switch that frees a node. It denies the LAN
everything but SSH, the API server, DHCP and ping, and admits the whole
cluster to every port, since a narrower cluster rule could not name Prometheus
and could lock out a node. The LAN reaches etcd only from the control-plane
addresses of the inventory, so a control plane at a new address needs its
`/32` here before it joins. A refused source mails
[`HostFirewallDrops`](logging.md#alerting). The rollout is in
[Host Firewall Rollout 2026-11-18](../operations/host-firewall-rollout-2026-11-18.md).

### Default ServiceAccount tokens

A pod that names no `serviceAccountName` lands on `default` and gets an API
token it has no use for, so `default-serviceaccounts.yaml` sets `automountServiceAccountToken: false` on it
in every namespace with nothing to preserve, empty namespaces included. Left
out: `kube-system`, where the control plane authenticates with client
certificates. `authentik-server`
runs on `default` by chart default and is covered: Authentik's Kubernetes
calls are worker tasks, which run under the `authentik` account.

The same rule applies to a chart workload whose own ServiceAccount has no
binding — Alertmanager, Cilium's Envoy and Argo CD's HAProxy switch the mount
off in their values — unless the process needs the in-cluster config to start
at all ([the External Secrets webhook](external-secrets.md#configuration)) or
the chart offers no switch (Rook's `rook-ceph-default` and `rook-ceph-rgw`).

### Agent RBAC

`claude-agent-rbac.yaml` gives the in-cluster Claude Code agents two
ClusterRoles for the `claude-reader` and `claude-writer` ServiceAccounts of the
`claude-homelab` namespace, the one session with cluster access. Cluster-scoped
RBAC cannot come from the `apps` project.

| Role | Bound | Grants | Leaves out |
| --- | --- | --- | --- |
| `claude-agent-read` | Cluster-wide, to both ServiceAccounts | `get`, `list`, `watch` on named core, workload and CRD kinds, `pods/log` included | Secrets, RBAC, webhooks, CRDs, `pods/exec`, Trivy's exposed-secret reports |
| `claude-agent-write` | Nowhere by the platform: the workloads repository adds a RoleBinding in each namespace a writer session may change | Create, update, patch and delete on pods, workloads, Services, ConfigMaps, jobs, autoscalers and disruption budgets | The above, plus PersistentVolumeClaims (deleting one deletes data), NetworkPolicies and routes (they change who may reach what, which is a pull request) |

- **Creating a pod reads every Secret in its namespace.** That is why the
  write role is bound per namespace, and the `apps` project cannot place a
  RoleBinding in the namespaces its [destinations](argocd.md#projects)
  exclude.
- **Each session is a `claude-<session>` namespace** declared in
  `pod-security.yaml`; `namespace-name-guard` keeps the `apps` project from
  creating one, so a new session is a platform pull request first. It reaches
  only its own [OpenBao prefix](openbao.md#kubernetes-auth), host name and
  certificate.
- **`claude-writer-pod-limits`** in `admission-policies.yaml` refuses any pod
  or pod template from a `claude-writer` ServiceAccount that uses the host's network, PID or IPC
  namespace, a `hostPath` volume, a privileged container, or a ServiceAccount
  other than `default` or the one the workload already runs as. A namespace
  without a Pod Security level allows all of those, and another
  ServiceAccount's token is that account's RBAC.
- **`pods/log` is cluster-wide**, so the reader sees whatever any component
  logs, platform namespaces included. Triage needs it, and a cluster-wide grant
  cannot leave out namespaces.

Kinds are listed, never wildcarded, so a new CRD is invisible to the agents
until someone adds it.

### Admission policies

Trivy reports a bad image after it is running; `admission-policies.yaml`
refuses or records it at admission, with the built-in
`ValidatingAdmissionPolicy` rather than a policy engine. The image policies
match Pods, their ephemeral containers and every controller that produces them, so a bad Deployment is
rejected at `kubectl apply` rather than failing quietly in its ReplicaSet. The
others close what RBAC and the `apps` project cannot express.

| Policy | Action | Why |
| --- | --- | --- |
| `image-tag-pinned` | `Deny` | A tag other than `latest`, or a digest. Nothing running violates it, and an unpinned image is the one Renovate can neither track nor roll back |
| `image-registry-allowed` | `Deny` | The registries the cluster pulls from; a reference with no registry counts as `docker.io` |
| `claude-writer-pod-limits` | `Deny` | Host access and borrowed ServiceAccounts from the agent writer, see [Agent RBAC](#agent-rbac) |
| `namespace-privileged-allowed` | `Deny` | A Namespace may carry `enforce: privileged` only if it is in the policy's list, the `privileged` rows above. The `apps` project creates its own Namespaces and their labels, so without it a workload could opt out of Pod Security |
| `authentik-foreign-objects` | `Deny` | In `authentik`, an object an Argo CD Application applied must come from `authentik` or `security`. Blueprints run with full Authentik rights, and Argo CD cannot deny a project one namespace's kinds, so this is what keeps `apps` out; objects with no `argocd.argoproj.io/tracking-id` annotation, from controllers and `kubectl`, are not matched |
| `namespace-name-guard` | `Deny` | A platform namespace, `kube-*`, `velero` or `claude-<session>` may be applied only by a platform Application: `apps` creates Namespaces, so a workload could otherwise relabel a platform one or take a name [OpenBao](openbao.md#kubernetes-auth) reaches by prefix |
| `obc-bucket-owner` | `Deny` | Only `backup/offsite-bucket` may set `bucketOwner` on an `ObjectBucketClaim`, and only to `offsite-reader`. Rook hands a claim the keys of the user it names, so any other claim could take over any S3 user, Rook's internal ones included — see [Off-cluster reader](rook-ceph.md#off-cluster-reader) |
| `object-store-user-scope` | `Deny` | Outside `rook-ceph`, a `CephObjectStoreUser` is named `<namespace>.<purpose>` and carries no admin `capabilities`. Its name is its S3 user ID, so a free choice would hand one namespace another's keys — see [Backup writers](rook-ceph.md#backup-writers) |
| `cnpg-cluster-backed-up` | `Deny` | A CloudNativePG `Cluster` names the Barman Cloud plugin as its WAL archiver |
| `cnpg-objectstore-offsite` | `Deny` | A Barman Cloud `ObjectStore` writes to `s3://offsite/<namespace>.<purpose>/`, the prefix TrueNAS pulls |
| `obc-backup-exempt` | `Deny` | A new `ObjectBucketClaim` says why it needs no backup; nothing copies a bucket off the cluster |
| `object-store-quota` | `Audit` and `Warn` | A new object store user or `ObjectBucketClaim` sets a size limit, see [Quotas](rook-ceph.md#quotas) |
| `certificate-issuer-scope` | `Deny` | Who may use a ClusterIssuer, see [Issuer scope](cert-manager.md#issuer-scope) |
| `controller-pod-limits` | `Deny` | No pods from the cert-manager controller, no host access for CloudNativePG's, see [Pods from the controllers](cert-manager.md#pods-from-the-controllers) |
| `route-hostnames-reserved` | `Deny` | Who may claim the platform, `ssh` and wollbi names on a route, see [Route hostnames](gateway-api.md#route-hostnames) |

`cnpg-cluster-backed-up`, `cnpg-objectstore-offsite` and `obc-backup-exempt` [keep a backup from being forgotten](../operations/backups.md#off-cluster-copy):
each yields to the annotation `homelab.wlkr.ch/backup-exempt` with the reason
as its value, and none checks an object being deleted, so an operator's
finalizer removal still goes through.

All but two carry `failurePolicy: Ignore`: an expression that errors lets the
request through rather than stopping every pod in the cluster. A real violation
is still refused. `claude-writer-pod-limits` and `controller-pod-limits` fail
closed, since they only ever match named identities.

## Usage

### Adding a registry

A pod from a registry outside the list is refused. If the registry is
intended, add it to the regular expression in `image-registry-allowed`.

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

A new policy is enforced on arrival: `policyAuditMode` in
`payload/platform/cilium/values.yaml` is off. Switched on, it makes every
agent evaluate each verdict and log it with `AUDIT` instead of dropping, and
those verdicts reach Loki the way drops do. The mode is cluster-wide, so
while it is on the older policies are not enforced either, which is why a
batch of new policies goes through it together.

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

### Changing the host firewall

A rule that might lock out a node is tried in audit mode first. Audit is a
setting of the running agent, not of the policy, and an agent restart drops
it, so set it per node and expect to set it again after any restart:

```bash
kubectl -n kube-system exec <agent-on-node> -c cilium-agent -- \
  sh -c 'cilium-dbg endpoint config $(cilium-dbg endpoint get -l reserved:host -o "jsonpath={\$[0].id}") PolicyAuditMode=Enabled'
```

Read what enforcement would refuse with the LogQL in the
[rollout log](../operations/host-firewall-rollout-2026-11-18.md#method), then
restart the agent to enforce again. `policyAuditMode` in the Cilium values is
not the switch: it stops enforcing every pod policy too.

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

!!! danger "A reboot can isolate a node a host policy selects"
    [Upstream](https://github.com/cilium/cilium/issues/48070) reports a selected node on this same combination, tunnel, kube-proxy replacement and WireGuard, dropping everything after a cold boot or a runtime audit-off, until an out-of-band fix. It is open and unreproduced here: all ten nodes passed a reboot while enforcing. A power cut would hit every node at once, and the [lockout runbook](#host-firewall-lockout) is the way back.

## Recovery

### Host firewall lockout

Work down until the node answers. An agent that cannot reach the API server
hears neither a policy change nor a label removal, so step 1 alone does not
free a node that is already cut off.

1. API server answering: take the node out of the policy. Argo CD does not own
   node labels, so nothing puts it back; remove the label once the cause is
   fixed. Use `--all` instead of a name for every node.

    ```bash
    kubectl label node <node> host-firewall.wlkr.ch/exempt=true
    ```

2. SSH answering: put the node in audit mode, which needs no API server and
   lasts until the agent restarts.

    ```bash
    ssh <node> sudo bash -s <<'EOF'
    id=$(crictl ps -q --label io.kubernetes.container.name=cilium-agent)
    crictl exec "$id" sh -c 'cilium-dbg endpoint config $(cilium-dbg endpoint get -l reserved:host -o "jsonpath={\$[0].id}") PolicyAuditMode=Enabled'
    EOF
    ```

3. Nothing answering, the API server up: nothing filters a node until its agent
   has started after boot, so stop the agent coming back. Run this from the
   workstation, then power-cycle the node, then do step 1 and start the kubelet
   again with `sudo systemctl unmask kubelet && sudo systemctl start kubelet`.

    ```bash
    until ssh -o ConnectTimeout=2 <node> sudo systemctl mask --now kubelet; do sleep 1; done
    ```

4. Every control plane cut off: the API server needs no CNI, so it comes up
   before the agent does. Power-cycle all three and exempt every node in that
   window.

    ```bash
    until kubectl label node --all host-firewall.wlkr.ch/exempt=true; do sleep 2; done
    ```

5. Still nothing: at the node's console, add `flatcar.autologin` to the boot
   entry in the GRUB menu, since `core` has no password, then run the inner
   command of step 2. Failing that, [rebuild the node](../operations/nodes.md#rebuilding-or-repartitioning-a-node).

6. Then [unseal OpenBao](../operations/nodes.md#after-any-node-reboot-unseal-openbao)
   and revert in Git: the policy first; the switch only if the firewall itself
   is the fault, since that revert rolls every agent again.
