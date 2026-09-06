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

## Components

- **kube-proxy replacement**: `kubeProxyReplacement: true`. Service routing
  happens in eBPF rather than iptables or IPVS, which is why `make install-core`
  deletes `kube-proxy` outright.
- **Gateway API**: Replaces a traditional Ingress controller — see
  [Gateway API](gateway-api.md).
- **LoadBalancer Pools**: `10.9.2.249` (apps) and `10.9.2.248` (infra),
  announced over L2 ARP so the rest of the LAN can find them.
- **WireGuard encryption**: `encryption.type: wireguard`, transparently, between
  nodes.
- **Hubble**: Observability with metrics and UI at `hubble.infra.k8s.wlkr.ch`,
  behind the [Authentik](authentik.md) proxy outpost.

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
    `make install-core` installs it by Helm with a version pinned in the `Makefile`, because it has to exist before ArgoCD does. ArgoCD then adopts it using the pin in `application.yaml`. Those two numbers are maintained separately and Renovate only tracks the second — a mismatch is not fatal, but it is exactly the kind of thing that makes a rebuilt cluster behave differently from the one it replaced.
