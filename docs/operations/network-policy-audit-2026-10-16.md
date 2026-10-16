---
description: "Dispositions from two weeks of network policy audit, 2026-10-02 to 2026-10-16, read before the flip to enforcement."
---

# Network Policy Audit 2026-10-16

Two weeks of `AUDIT` verdicts, 195k records, read before
`policyAuditMode` went off. The method is the
[rollout](../platform/security-policies.md#rollout); the first reading is
[Network Policy Audit 2026-09-27](network-policy-audit-2026-09-27.md). No
namespace with pods is without a policy, and every pod endpoint on the
nodes checked is selected in both directions.

## Fixed

| Flow | Disposition |
| --- | --- |
| Velero and its data movers to `s3.infra.k8s.wlkr.ch`, 3.7k requests a day since 2026-10-04 | **Rule.** Not an audit verdict: the Gateway's Envoy checked Velero's egress against the RGW pod and answered `403` itself, which audit mode never sees. The backup location was Unavailable and every daily backup `FailedValidation`. Egress to the RGW pod on 8080 in `backup.yaml` |
| Pods in their first seconds, under the `init` identity: Trivy scan jobs' lookups and their calls to the Trivy server, the kubelet's probes, lookups landing on a CoreDNS pod that had just started; 27k records | **Cluster-wide policy** `init`: the node, DNS out and DNS in. A scan job's call to the Trivy server in the same seconds retransmits until the identity arrives |
| Every header of every proxied request in the export, `X-Vault-Token`, bearer tokens and cookies included | **Redacted** with `hubble.redact`; see [Cilium](../platform/cilium.md#configuration). Records written before the change keep them until Loki's retention drops them |

## Nothing to do

| Flow | Why |
| --- | --- |
| Rook mons, mgrs and crash collectors to `10.108.70.163`, `10.108.19.154` and `10.102.93.197` on 3300, 50k records from 2026-10-13 to the restarts of 2026-10-16 | Service addresses of mons that no longer exist; the daemons kept them until they restarted. A connection to an address with no backend fails with or without a policy |
| OSDs and RGW to pod addresses on 6800–6808 classified `world`, after an OSD restart | Addresses of pods that had just gone |
| The CloudNativePG operator to every instance on 8000, a few dozen at a time, and other in-cluster flows at agent restarts and node joins (2026-10-10, 2026-10-16) | The rule exists; the peer's identity had not reached the agent yet |
| A name a `toFQDNs` rule allows, one `SYN` now and then: Authentik to `www.gravatar.com`, the repo-server to the chart repositories, Dependency-Track to its package registries, scan jobs to the registry CDNs, Alertmanager to the mail relay | The DNS proxy's hand-off, as in the first reading. Gravatar stays a database setting |
| Prometheus and metrics-server to the five new nodes as `world`, 2026-10-10 | The nodes joining |
| Rook's mgr and detect-version Job to a control-plane node's API server as `host`, a handful a day | The node identity briefly without `reserved:kube-apiserver`; watch for it as `DROPPED` |

## Decisions left open

| Flow | Question |
| --- | --- |
| Agent sessions to Prometheus, Hubble Relay, OpenBao and Argo CD in the cluster, and to PyPI, npm, `dl.k8s.io` and `get.helm.sh` | Each session's reach is its chart's toggles, so these now drop. Granting one is a change to the session, not to a namespace policy |
| Agent sessions to `http-intake.logs.us5.datadoghq.com` | The `claude-agent` chart allows Claude Code's error reporting by name; telemetry here is switched off at the source, not allowed |
