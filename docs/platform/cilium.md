---
description: "Cilium as the CNI, providing Gateway API, WireGuard encryption, L2 announcements, and Hubble observability."
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
| Stage | `02-network`, after the Gateway API CRDs it renders against |
| Depends on | Gateway API and Prometheus operator CRDs (`01-crds`), and a `k8sServiceHost` that answers |
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
| `encryption.type: wireguard` | Transparent node-to-node encryption |
| Hubble, behind the [Authentik](authentik.md) proxy outpost | Hubble has no authentication of its own; the HTTPRoute points at the outpost and a ReferenceGrant in `payload/platform/authentik/` permits the cross-namespace reference |
| Hubble metrics labelled by namespace | The chart's Hubble dashboards filter on source and destination namespace and show nothing without them; namespace keeps the series count to the square of the namespace count. The HTTP metric adds workloads, but only for traffic Cilium proxies at L7, which is the Gateway. The agent reads the list at startup, so a change lands on reboot or upgrade, not on sync |
| `hubble.export.static` | Every flow with a `DROPPED`, `ERROR` or `AUDIT` verdict goes to a file on the node that [Alloy](logging.md) tails into Loki, with a field mask that keeps identities and ports and drops the rest. Hubble's own buffer is minutes deep; this is the record of what a policy refused after the fact, and it costs nothing while nothing is dropped. Forwarded flows are not exported: thousands a second on a cluster this size. Like the metrics list, the agent reads it at startup |
| `prometheusrule.yaml` | `HubblePolicyDrops`: fifteen minutes of `POLICY_DENIED` drops between two namespaces, above a trickle. `VLAN_FILTERED` and `STALE_OR_UNROUTABLE_IP` dominate the raw counter and are the LAN, not a policy |
| `cilium-agent` without a memory limit | It is the one process whose death takes pod networking with it, and it cannot be sized from a day of steady state |
| `trustCRDsExist: true` | The chart otherwise refuses to render while `monitoring.coreos.com/v1` is missing: the bootstrap install, a render before `01-crds` has synced, and the diff preview's throwaway cluster |

## Installation

Cilium is installed twice, in a sense. `make install-cilium` installs it by
Helm, because no pod runs without a CNI and ArgoCD is a pod; ArgoCD then
adopts the release. The version comes from `targetRevision` in
`application.yaml` — see [Version pins](../architecture/gitops.md).

The bootstrap install uses the same `values.yaml`, and has to: the agent and
operator do not restart when `cilium-config` changes, so a slimmer bootstrap
config would keep running after ArgoCD "fixed" it. The Gateway API CRDs go in
first for the same reason — the operator checks for them once, at startup. The
one difference is the three `serviceMonitor.enabled` flags, which `make` turns
off because the Prometheus operator CRDs arrive through ArgoCD; ArgoCD adds
the monitors back on adoption, rolling the agent and operator once.

## Health check

```bash
kubectl -n kube-system exec ds/cilium -- cilium status --brief
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
