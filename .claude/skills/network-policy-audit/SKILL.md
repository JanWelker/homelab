---
name: network-policy-audit
description: Turn Hubble's audited or dropped verdicts into CiliumNetworkPolicy rules, tighten the L7 rules from the recorded requests, and find policies that could be consolidated or lifted into one cluster-wide policy. Use when the user says "what did the policies block this week", "generate the missing rules from the audit log", "turn the AUDIT verdicts into CNPs", "tighten the http rules", "can these network policies be consolidated", "which rules could be global like DNS", "we are past the audit week, switch to enforcing", or after any new component lands and something is Degraded with policy in the picture. Covers reading the verdicts from Loki, the script that proposes rules per policy file, telling a real denial from a reply packet or an L7 refusal, the consolidation checks, and the flip from audit to enforcing.
---

# network-policy-audit

Every namespace has default-deny policies in both directions under
`payload/platform/security/network-policies/` (platform) and
`<app>/networkpolicy.yaml` (homelab-apps). Their shape and the audit-first
rollout are in `docs/platform/security-policies.md`; this skill is how to
read what the cluster refused and write it back as rules. Nothing here
changes the cluster: the output is YAML for a pull request.

`policy_audit.py` next to this file does the mechanical part. It needs
`uv run python` (PyYAML from the project venv) and `kubectl` on
`output/kubeconfig`.

## 1. Fetch the verdicts

```bash
export KUBECONFIG=$PWD/output/kubeconfig
kubectl -n logging port-forward svc/loki 13100:3100 &
uv run python .claude/skills/network-policy-audit/policy_audit.py fetch --since 7d > /tmp/audit.jsonl
```

`fetch` pages `{job="hubble", verdict="AUDIT"}` out of Loki. Once
`policyAuditMode` is off the same lines carry `verdict="DROPPED"` and
`drop_reason_desc="POLICY_DENIED"`; feed them the same way. For the last
few minutes without Loki, `hubble observe --verdict AUDIT -o json` produces
the same records.

Records written before #856 have no `is_reply`, `identity` or
`destination_names`; the script then guesses replies from the port and
shows a peer it cannot classify as `unknown` with its address.

## 2. Propose rules

```bash
uv run python .claude/skills/network-policy-audit/policy_audit.py propose /tmp/audit.jsonl
```

One section per namespace and direction, headed by the file the rule
belongs in, one snippet per (workload, peer, port) with the count. Read
each against three explanations before pasting anything:

| The script says | Meaning | Do |
| --- | --- | --- |
| `already allowed by policy '…'` | A reply packet from the moment the policy loaded, or an L7 refusal that the L4 rule admitted | Nothing for the first; for the second, §3 |
| A pod peer with a port above 32767 | The answer half of a scrape or query | Nothing; the export now marks these `is_reply` |
| `rook-ceph-operator -> … 6800` | The operator's two stale mgr addresses, `STALE_OR_UNROUTABLE_IP` | Nothing; predates every policy |
| `unknown <address>` | Export without identity; a node address on 6443 is `kube-apiserver`, a LAN or Internet address is `world` | Name it: `toEntities` for nodes, `toFQDNs` for the Internet, `toCIDR` for the LAN and the Gateway addresses |
| A Job or CronJob pod | The selector of the workload policy does not cover it | Add its `app.kubernetes.io/name`, or give the pod template one |

A snippet's selector line names the workload; put the rule in that
workload's policy when the namespace has one, in `default-*` when the
whole namespace needs it. A world destination is written as a name: the
Gateway's own addresses count (`auth.k8s.wlkr.ch` is `world` to Cilium),
and an address the DNS proxy never saw arrives without a name until the
DNS rule exists for that pod.

The proposed rule is L4. Add `rules.http: [{}]` on the receiving side when
the port is plaintext HTTP and the peer is another namespace, the same
as every other rule of that kind.

## 3. Tighten the L7 rules

Every `http: [{}]` was written to be replaced. The requests it saw are in
Loki, exported by the same file:

```logql
{job="hubble"} | json | flow_l7_http_method != "" | line_format "{{.flow_source_namespace}} -> {{.flow_destination_namespace}}:{{.flow_l4_TCP_destination_port}} {{.flow_l7_http_method}} {{.flow_l7_http_url}}"
```

Group by destination and port over the whole window, then write the
method and path list:

```yaml
rules:
  http:
    - method: GET
      path: /metrics$
```

Paths are regular expressions matched against the path without the query
string; anchor them. A path the rule does not name is answered `403` at
once and never audited, so leave a port at `[{}]` rather than guess, and
keep the Gateway's `ingress` rules at L4: the HTTPRoute is their L7 filter.

## 4. Consolidate

```bash
uv run python .claude/skills/network-policy-audit/policy_audit.py consolidate
```

Five checks per namespace and one across the cluster:

| Finding | Meaning |
| --- | --- |
| Two policies select the same endpoints | One policy with both directions reads as one unit. `default-ingress` and `default-egress` are the exception: the `security` Application never prunes, so a rename leaves the old object behind |
| Rules share a peer and differ in ports | One rule with a port list |
| A workload rule repeats a `default-*` rule | Drop it from the workload policy |
| Several `toFQDNs` rules share ports | One name list |
| A peer is admitted by many namespaces | Keep the wording identical across files, since a reader compares them |
| A rule appears in most namespaces' whole-namespace policies | Cluster-wide candidate, below |

The cluster-wide candidates today are the intra-namespace rules, the node
for probes, DNS through the proxy and the API server. Lifting one into a
`CiliumClusterwideNetworkPolicy` is a trade: the per-namespace file stops
being the complete record of what its namespace may do. If it is worth
it, the policy must carry `enableDefaultDeny: {ingress: false, egress: false}`;
without that, an `endpointSelector: {}` policy with an egress rule puts
every pod in the cluster into egress default-deny, including namespaces
that have no policy yet.

## 5. Ship

One pull request per policy file, the rule in the file's `description`
in a clause, Hubble's line for the flow in the PR body. Workload
namespaces are pull requests in `homelab-apps`. After the audit week:
`policyAuditMode: false` in `payload/platform/cilium/values.yaml`, then
the agent restart from the runbook, and from then on the verdict to fetch
is `DROPPED`.
