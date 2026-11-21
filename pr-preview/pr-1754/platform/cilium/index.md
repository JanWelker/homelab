# Cilium

Cilium is the CNI: it replaces `kube-proxy`, terminates ingress through Gateway API, hands out LoadBalancer addresses on a network with no cloud load balancer, encrypts node-to-node traffic, and shows what is talking to what. A broken Cilium is never a small problem.

## At a glance

|  |  |
| --- | --- |
| Namespace | `kube-system` |
| Depends on | The Gateway API and Prometheus operator CRDs it renders against, and a `k8sServiceHost` that answers |
| If it is down | Everything. No CNI, no service routing, no ingress, no LoadBalancer addresses |
| Health check | `kubectl -n kube-system exec ds/cilium -- cilium status --brief` |
| UI | `hubble.infra.k8s.wlkr.ch` (Hubble); the same flows in 3D across namespaces at [Flowscape](<https://homelab-apps.wlkr.ch/flowscape/>) |
| Files | `payload/platform/cilium/` |

## Configuration

| Setting | Why |
| --- | --- |
| `kubeProxyReplacement: true` | Service routing in eBPF. `proxy.disabled` in `ansible/templates/kubeadm.yaml.j2` keeps `kube-proxy` out at init and on every `kubeadm upgrade apply` |
| `k8sServiceHost` as a literal IP | With `kube-proxy` gone Cilium cannot reach the API server through a Service, so it names the [control-plane VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>) — see [Pitfalls](<#pitfalls>) |
| Gateway API | Replaces an Ingress controller — see [Gateway API](<https://homelab.wlkr.ch/platform/gateway-api/index.md>) |
| `lb-pools.yaml` | One address each for the apps and infra Gateways, announced over L2 ARP so the LAN can find them |
| `bgp.yaml` | The same two addresses as `/32` routes over eBGP from every control-plane node to the router. The router sends traffic from other VLANs, VPN clients and port forwards to all three nodes and drops a dead one after the hold time, where L2 moves one lease. L2 stays because LAN hosts ARP for the addresses and never ask the router. The ASNs and timers are in `bgp.yaml`. The router side is FRR on the UDM Pro: `make config` renders it from the inventory's `router_*` variables into `output/router/frr-bgp.conf`, so a firmware reset is recovered by pasting that file with the [password](<#bgp-session-password>) added |
| `encryption.type: wireguard` | Pod-to-pod only; `nodeEncryption` is deprecated |
| Hubble, behind the [Authentik](<https://homelab.wlkr.ch/platform/authentik/index.md>) proxy outpost | Hubble has no authentication of its own; the HTTPRoute points at the outpost through a ReferenceGrant in `payload/platform/authentik/` |
| Hubble metrics labelled by namespace | The chart's Hubble dashboards show nothing without them; namespace keeps the series count to the square of the namespace count. The HTTP metric adds workloads, but only for the Gateway's proxied traffic. |
| `hubble.export.static` | Every flow with a `DROPPED`, `ERROR` or `AUDIT` verdict, and every HTTP request a policy proxies at L7, goes to a file on the node that [Alloy](<https://homelab.wlkr.ch/platform/logging/index.md>) tails into Loki, with a field mask that keeps identities, ports and the request line. Hubble's own buffer is minutes deep; this is the record the L7 rules are tightened from. Other forwarded flows are thousands a second and not exported. |
| `hubble.redact` | Every header except `User-Agent` and `Content-Type`, and the password of a basic-auth URL, is replaced before a flow leaves the agent, for the export file and Relay alike. L7 rules proxy requests that carry credentials: `X-Vault-Token`, bearer tokens, signed S3 requests, cookies. The method, path and query survive |
| `hostFirewall.enabled: true` | Filters traffic to the node's own addresses, which is every host-networked port, through `CiliumClusterwideNetworkPolicy` objects that select nodes. Nothing is denied on IPv4 until a policy selects the node; IPv6 differs, see [Pitfalls](<#pitfalls>). Rollout and policy in [Host firewall](<https://homelab.wlkr.ch/platform/security-policies/index.md#host-firewall>) |
| `policyAuditMode: false` | Policies are enforced. On, verdicts are logged and not enforced for a rollout's observation week — see [Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#rollout>). Cluster-wide, and blind to L7 rules |
| `rollOutCiliumPods` and the four `rollOutPods` | The agent, Envoy, the operator, Relay and the UI read their ConfigMap once, at startup. With a checksum of it on the pod template, a merged value rolls the pods and is live within minutes, and a value the agent refuses fails at merge time, not at the next unrelated restart. The price is that every change to `cilium-config` rolls the CNI on every node: running pods keep their networking, policy updates pause per node while its agent is down, and the [`k8sServiceHost` pitfall](<#pitfalls>) bites at merge, not later |
| `updateStrategy.rollingUpdate.maxUnavailable: 1` | The chart default restarts the agent on two nodes at once, which can be two of the three control planes that hold the L2 leases and BGP sessions; one at a time keeps every Gateway address announced |
| `operator.replicas` above one | The operator owns Gateway API reconciliation, LoadBalancer IP allocation and the `cilium-secrets` sync: with one replica, a drain or crash of its node stalls all three until the reschedule. The chart's hostname anti-affinity keeps the replicas apart, so the standby is already running — see the [operator pitfall](<#pitfalls>) |
| The operator's cluster-wide Secret read | The chart grants `get`, `list` and `watch` on all Secrets whenever Gateway API or `bgpControlPlane` is on, with no value to narrow it: the operator copies the certificate Secrets a Gateway names, in any namespace, into `cilium-secrets`. The [log alert](<https://homelab.wlkr.ch/platform/logging/index.md#alerting>) allowlists it |
| `lb-pools.yaml` on `cilium.io/v2` | The storage version for `CiliumLoadBalancerIPPool`; `v2alpha1` is served as deprecated and warns on every sync. `CiliumL2AnnouncementPolicy` has no `v2` yet and stays |
| `cilium-agent` memory limit | It is the one process whose death takes pod networking with it, so it was sized from a week of measurement, see the [platform rule](<https://homelab.wlkr.ch/platform/index.md>) |
| `trustCRDsExist: true` | The chart otherwise refuses to render while `monitoring.coreos.com/v1` is missing: at bootstrap, before `prometheus-operator-crds` has synced, and in the diff preview |

## Usage

### Installation

Cilium is installed twice, in a sense. `make install-cilium` installs it by Helm, because no pod runs without a CNI and ArgoCD is a pod; ArgoCD then adopts the release. The version comes from `targetRevision` in `application.yaml` — see [Version pins](<https://homelab.wlkr.ch/architecture/gitops/index.md>).

The bootstrap install uses the same `values.yaml`, and has to: the pods roll whenever `cilium-config` changes, so a slimmer bootstrap config would restart every agent the moment ArgoCD adopts the release. The Gateway API CRDs go in first because the operator checks for them once, at startup. The one difference is the three `serviceMonitor.enabled` flags, which `make` turns off because the Prometheus operator CRDs arrive through ArgoCD; ArgoCD adds the monitors back on adoption, rolling the agent and operator once.

### BGP session password

The session is TCP MD5 authenticated with a typed password, since the router needs the same one. Setting or rotating it breaks BGP, and both Gateways with it, until both ends match; L2 still announces them to the flat LAN.

1. `make bao-secrets`, answering for `kv/cilium/bgp`.
2. On the router, in the live FRR and `output/router/frr-bgp.conf`, then `clear bgp *`:

   ```text
   neighbor cilium password <password>
   ```
3. Check the session as in the [Health check](<#health-check>).

## Health check

```bash
kubectl -n kube-system exec ds/cilium -- cilium status --brief
```

The BGP session on each control-plane node, `established` with two routes advertised. `bgp.yaml` selects the control planes only, so ask that node's agent rather than whichever pod `ds/cilium` resolves to:

```bash
kubectl -n kube-system exec "$(kubectl -n kube-system get pod -l k8s-app=cilium \
  --field-selector spec.nodeName=<control-plane> -o name)" -- cilium bgp peers
```

Hubble answers *what did I just break?* during a network policy rollout — see [Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#usage>). The `hubble` CLI talks to Relay through a port-forward on its default port:

```bash
kubectl -n kube-system port-forward deploy/hubble-relay 4245:4245 &
hubble observe --verdict DROPPED --follow
```

The same verdicts, for the whole Loki retention window, in Grafana:

```text
{job="hubble", verdict="DROPPED"} | json | flow_drop_reason_desc="POLICY_DENIED"
```

## Pitfalls

> [!CAUTION]
>
> **`k8sServiceHost` pointed at an address that does not answer takes the cluster down**
>
> Every Cilium pod loses the API server at once, and cluster networking goes with it. The change ordering is in [Control Plane VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>).

> [!WARNING]
>
> **An operator that starts without the API server leaves the Gateways without certificates**
>
> cilium-operator checks for the Gateway API CRDs once at startup; if that check fails it runs without its Gateway controller until the next restart, and `cilium-secrets` empties. Envoy then resets the TLS handshake on every node without a cached copy, so clients fail depending on the node the router hashes them to. `CiliumGatewayCertificatesMissing` fires; `kubectl -n kube-system rollout restart deploy/cilium-operator` recovers it.

> [!CAUTION]
>
> **A chart default that disappears stays in `cilium-config`**
>
> The bootstrap install's `helm` field manager still co-owns every `cilium-config` key it wrote, so ArgoCD's server-side apply cannot delete one the chart stops rendering. Dropping the `envoy.xdsMode` default left the agents on `ads` while Envoy's bootstrap switched to split xDS, so no Gateway or L7 policy forwarded. Pin such a value in `values.yaml` rather than relying on the default.

> [!WARNING]
>
> **The host firewall drops all node IPv6, with or without a policy**
>
> With `enable-ipv6: false` the datapath has no IPv6 branch, so on the native device and `cilium_host` every IPv6 packet is dropped as `DROP_UNKNOWN_L3` in both directions, before any policy or audit mode ([upstream](<https://github.com/cilium/cilium/issues/47307>)). A client that does not fall back to IPv4 stalls until the node's SLAAC address expires. The drops reach Loki as `DROPPED` flows with a reason other than `POLICY_DENIED`, so `HubblePolicyDrops` and `HostFirewallDrops` stay quiet.
