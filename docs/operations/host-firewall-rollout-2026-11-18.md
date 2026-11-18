---
description: "The host firewall's audit week, 2026-10-31 to 2026-11-18, what it would have refused, and the per-node switch to enforcing with a reboot of each node."
---

# Host Firewall Rollout 2026-11-18

The [host firewall](../platform/security-policies.md#host-firewall) ran in
audit mode on all ten nodes from 2026-10-31, held there by a DaemonSet that
re-enabled audit after every agent restart. On 2026-11-18 each node in turn was
switched to enforcing by restarting its agent, then rebooted through Kured.

## Method

What enforcement would have refused: audit verdicts addressed to a host
identity. Pods and nodes are admitted, so anything here came from the LAN.

```logql
{job="hubble", verdict="AUDIT"} | json dst_id="flow.destination.identity", src="flow.IP.source", dst="flow.IP.destination", tcp="flow.l4.TCP.destination_port", udp="flow.l4.UDP.destination_port" | dst_id="1" | line_format "{{.src}} -> {{.dst}} {{.tcp}}{{.udp}}"
```

## Audit verdicts

Six in eighteen days; no rule was added.

| When | Source → node:port | What it was | Disposition |
| --- | --- | --- | --- |
| 10-31, three | Workstation → two workers :9100 | node-exporter from the LAN, the exposure the firewall exists to close | Refused |
| 11-04, three | Router → control planes :179 | The router opening BGP after a reconnect; the agent dials out and nothing listens on 179 | Refused; the session still comes up |

## Enforcing

| Order | Nodes | Note |
| --- | --- | --- |
| 1 | One worker, then a second | Each rebooted alone before the next was touched |
| 2 | The other five workers | Switched together, rebooted one at a time by Kured |
| 3 | The control planes | The one holding the control-plane address last |

Every node came back `Ready` within about two minutes of its reboot with
audit off, SSH, its pods and, on control planes, etcd, the API server and the
BGP session working. The only drops after a boot were a few seconds of
replies to one connection opened before the agent started; see the
behaviour table in the [host firewall](../platform/security-policies.md#host-firewall)
section.

## Found on the way

| Finding | Where it went |
| --- | --- |
| A drain moved Prometheus onto an 8 GB worker that its 2.3 GiB request fitted and its 4.4 GiB working set did not; the node swapped into `NotReady` | Request raised to the measured median, [Monitoring](../platform/monitoring.md#grafana) |
| A drain of the Authentik database primary failed over instead of switching over, three minutes without sign-in | Replicated clusters keep their PodDisruptionBudgets, [the contract](../platform/cloudnative-pg.md#the-contract) |
| Drops on a node's own address mailed with no node or source | `HostFirewallDrops`, [Logging](../platform/logging.md#alerting) |
| Kured waited up to half an hour between reboots | `period` and `drainTimeout` from measured reboots, [Kured](../platform/kured.md) |
