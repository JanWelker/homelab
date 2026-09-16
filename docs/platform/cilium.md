---
description: "Cilium as the CNI, providing Gateway API, WireGuard encryption, L2 announcements, and Hubble observability."
---

# Cilium

Cilium is the CNI, and on this cluster it is considerably more than that. It
replaces `kube-proxy`, terminates ingress traffic through Gateway API, hands out
LoadBalancer addresses on a network with no cloud load balancer, encrypts
node-to-node traffic, and shows you what is actually talking to what.

Four components' worth of responsibility in one DaemonSet. That consolidation is
the whole argument for it on bare metal — and also the reason a broken Cilium is
never a small problem.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` |
| Sync wave | `-1`, after the Gateway API CRDs it renders against |
| Depends on | Gateway API CRDs (`-10`), the Prometheus operator CRDs from `make install-cilium`, and a `k8sServiceHost` that answers |
| If it is down | Everything. No CNI, no service routing, no ingress, no LoadBalancer addresses |
| Health check | `kubectl -n kube-system exec ds/cilium -- cilium status --brief` |
| UI | `hubble.infra.k8s.wlkr.ch` (Hubble) |

## Components

- **kube-proxy replacement**: `kubeProxyReplacement: true`. Service routing
  happens in eBPF rather than iptables or IPVS, which is why kubeadm never
  deploys `kube-proxy`: `proxy.disabled` in `ansible/templates/kubeadm.yaml.j2`
  keeps it out at init and on every `kubeadm upgrade apply`.
- **Gateway API**: Replaces a traditional Ingress controller — see
  [Gateway API](gateway-api.md).
- **LoadBalancer Pools**: `10.9.2.249` (apps) and `10.9.2.248` (infra),
  announced over L2 ARP so the rest of the LAN can find them.
- **WireGuard encryption**: `encryption.type: wireguard`, transparently, between
  nodes.
- **Hubble**: Observability with metrics and UI at `hubble.infra.k8s.wlkr.ch`,
  behind the [Authentik](authentik.md) proxy outpost, because Hubble has no
  authentication of its own. The HTTPRoute points at the outpost, which proxies
  to hubble-ui; a ReferenceGrant in `payload/platform/authentik/` permits the
  cross-namespace reference.

## Hubble metrics

The flow-level metrics carry source and destination namespace, which the
chart's Hubble dashboards in Grafana filter on and show nothing without; see
[Monitoring](monitoring.md#dashboards). Namespace rather than workload or IP
keeps the series count to the square of the namespace count. The HTTP metric
adds workloads for the L7 dashboard, but only has data for traffic Cilium
proxies at L7, which here is the Gateway.

The agent reads this list at startup, so a change lands as nodes reboot or
Cilium upgrades, not when ArgoCD syncs.

## Resource limits

The operator, Envoy, Hubble Relay and Hubble UI have memory limits sized at
roughly 2.5x their measured peak working set. The `cilium-agent` DaemonSet is
deliberately left without one: it peaked at 337Mi, it is the one process on the
node whose death takes pod networking with it, and a day of steady state is not
enough to size something on that critical path.

## The one setting that will ruin your day

`k8sServiceHost` in `values.yaml` is a literal IP address, because with
`kube-proxy` gone Cilium cannot reach the API server through a Service. It has to
be told where the control plane lives.

Point it at an address that does not answer and every Cilium pod loses the API
server at once. Cluster networking goes with it, including whatever you were
using to fix the problem. This is the single most effective way to take the
whole cluster down from one line of YAML, so treat changes to it with the
respect they deserve — the ordering is spelled out in
[Control Plane VIP](../operations/control-plane-vip.md).

!!! tip "Hubble earns its keep during a network policy rollout"
    `hubble observe --verdict DROPPED --follow` answers the question every default-deny policy raises — *what did I just break?* — in seconds rather than in an hour of guessing. See [Security Policies](security-policies.md#rolling-this-out-safely).

## Directory Structure

```text
cilium/                # CNI + Gateway API Controller
├── application.yaml   # ArgoCD Application (Helm chart)
├── values.yaml        # Helm values
├── lb-pools.yaml      # CiliumLoadBalancerIPPool + L2 Policy
├── rbac-gateway-fix.yaml # RBAC fix for Gateway API
└── httproute.yaml     # Hubble UI route
```

!!! note "Cilium is installed twice, sort of"
    `make install-core` installs it by Helm, because it has to exist before ArgoCD does, and ArgoCD then adopts it. There is only one number: the `Makefile` reads `targetRevision` straight out of `application.yaml` instead of keeping a pin of its own, so a rebuilt cluster cannot quietly land on a different Cilium than the one it replaced.
