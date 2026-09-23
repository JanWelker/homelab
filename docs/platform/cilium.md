---
description: "Cilium as the CNI, providing Gateway API, WireGuard encryption, L2 and BGP announcements, and Hubble observability."
---

# Cilium

Cilium is the CNI, and on this cluster considerably more: it replaces
`kube-proxy`, terminates ingress through Gateway API, hands out LoadBalancer
addresses on a network with no cloud load balancer, encrypts node-to-node
traffic, and shows what is talking to what. That consolidation is the argument
for it on bare metal, and the reason a broken Cilium is never a small problem.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` |
| Depends on | The Gateway API and Prometheus operator CRDs it renders against, and a `k8sServiceHost` that answers |
| If it is down | Everything. No CNI, no service routing, no ingress, no LoadBalancer addresses |
| Health check | `kubectl -n kube-system exec ds/cilium -- cilium status --brief` |
| UI | `hubble.infra.k8s.wlkr.ch` (Hubble) |
| Files | `payload/platform/cilium/` |

## Configuration

| Setting | Why |
| --- | --- |
| `kubeProxyReplacement: true` | Service routing in eBPF. `proxy.disabled` in `ansible/templates/kubeadm.yaml.j2` keeps `kube-proxy` out at init and on every `kubeadm upgrade apply` |
| `k8sServiceHost` as a literal IP | With `kube-proxy` gone Cilium cannot reach the API server through a Service — see [Pitfalls](#pitfalls) |
| Gateway API | Replaces an Ingress controller — see [Gateway API](gateway-api.md) |
| `lb-pools.yaml` | One address each for the apps and infra Gateways, announced over L2 ARP so the LAN can find them |
| `bgp.yaml` | The same two addresses as `/32` routes over eBGP from every control-plane node to the router. The router forwards traffic it routes, from other VLANs, VPN clients and port forwards, to all three nodes at once and drops a dead one after the 9s hold time, where L2 moves one lease. L2 stays because the addresses sit inside the flat LAN, whose hosts ARP for them and never ask the router. The router side is FRR on the UDM Pro peering with the three node IPs, ASN 65000 against 65001 |
| `encryption.type: wireguard` | Transparent node-to-node encryption |
| Hubble, behind the [Authentik](authentik.md) proxy outpost | Hubble has no authentication of its own; the HTTPRoute points at the outpost and a ReferenceGrant in `payload/platform/authentik/` permits the cross-namespace reference |
| Hubble metrics labelled by namespace | The chart's Hubble dashboards filter on source and destination namespace and show nothing without them; namespace keeps the series count to the square of the namespace count. The HTTP metric adds workloads, but only for traffic Cilium proxies at L7, which is the Gateway. |
| `hubble.export.static` | Every flow with a `DROPPED`, `ERROR` or `AUDIT` verdict, and every HTTP request a policy proxies at L7, goes to a file on the node that [Alloy](logging.md) tails into Loki, with a field mask that keeps identities, ports and the request line and drops the rest. Hubble's own buffer is minutes deep; this is the record of what a policy refused after the fact, and of the requests the L7 rules are tightened from. Other forwarded flows are not exported: thousands a second on a cluster this size, where the proxied requests are a few. |
| `policyAuditMode` | Verdicts are logged, not enforced, for the observation week of a policy rollout — see [Security Policies](security-policies.md#rollout). Cluster-wide, and blind to L7 rules |
| `rollOutCiliumPods` and the four `rollOutPods` | The agent, Envoy, the operator, Relay and the UI read their ConfigMap once, at startup. With a checksum of it on the pod template, a merged value rolls the pods and is live within minutes, and a value the agent refuses fails at merge time, while someone is watching, not at the next unrelated restart. The price is that every change to `cilium-config` is a rolling restart of the CNI on all six nodes: running pods keep their networking, policy updates pause per node for the seconds its agent is down, and the [`k8sServiceHost` pitfall](#pitfalls) bites at merge, not later |
| `prometheusrule.yaml` | `HubblePolicyDrops`: fifteen minutes of `POLICY_DENIED` drops between two namespaces, above a trickle. `VLAN_FILTERED` and `STALE_OR_UNROUTABLE_IP` dominate the raw counter and are the LAN, not a policy |
| `cilium-agent` without a memory limit | It is the one process whose death takes pod networking with it, and it cannot be sized from a day of steady state |
| `trustCRDsExist: true` | The chart otherwise refuses to render while `monitoring.coreos.com/v1` is missing: the bootstrap install, a render before `prometheus-operator-crds` has synced, and the diff preview's throwaway cluster |

## Installation

Cilium is installed twice, in a sense. `make install-cilium` installs it by
Helm, because no pod runs without a CNI and ArgoCD is a pod; ArgoCD then
adopts the release. The version comes from `targetRevision` in
`application.yaml` — see [Version pins](../architecture/gitops.md).

The bootstrap install uses the same `values.yaml`, and has to: the pods roll
whenever `cilium-config` changes, so a slimmer bootstrap config would restart
every agent the moment ArgoCD adopts the release. The Gateway API CRDs go in
first because the operator checks for them once, at startup. The
one difference is the three `serviceMonitor.enabled` flags, which `make` turns
off because the Prometheus operator CRDs arrive through ArgoCD; ArgoCD adds
the monitors back on adoption, rolling the agent and operator once.

## Health check

```bash
kubectl -n kube-system exec ds/cilium -- cilium status --brief
```

The BGP session on each control-plane node, `established` with two routes
advertised:

```bash
kubectl -n kube-system exec ds/cilium -- cilium bgp peers
```

Hubble answers *what did I just break?* during a network policy rollout — see
[Security Policies](security-policies.md#usage):

```bash
hubble observe --verdict DROPPED --follow
```

The same verdicts, for the whole Loki retention window, in Grafana:

```logql
{job="hubble", verdict="DROPPED"} | json | flow_drop_reason_desc="POLICY_DENIED"
```

## Pitfalls

!!! danger "`k8sServiceHost` pointed at an address that does not answer takes the cluster down"
    Every Cilium pod loses the API server at once, and cluster networking goes with it — including whatever you were using to fix the problem. The change ordering is in [Control Plane VIP](../operations/control-plane-vip.md).
