---
description: "Every port listening on a node's own addresses on 2026-10-30, who connected to it, and whether the host firewall admits it from the LAN."
---

# Host Firewall Inventory 2026-10-30

`ss -lntu` and the established peers of each listening port on all ten nodes,
read before the [host firewall](../platform/security-policies.md#host-firewall)
selected any of them. "Cluster" is every node and pod; "LAN" is everything
else, Cilium's `world`.

## Listening on a node address

| Port | Listener | Nodes | Connections seen from | Admit from |
| --- | --- | --- | --- | --- |
| 22/TCP | sshd | all | Workstation | LAN, cluster |
| 6443/TCP | kube-apiserver, kube-vip's address on the holder | control plane | Workstation, every node, pods | LAN, cluster |
| 2379/TCP | etcd client | control plane | Loopback only | Cluster, node range |
| 2380/TCP | etcd peer | control plane | The other members | Cluster, node range |
| 2381/TCP | etcd metrics, plain HTTP | control plane | Prometheus | Cluster |
| 10250/TCP | kubelet | all | API server, Prometheus, metrics-server | Cluster |
| 10257/TCP, 10259/TCP | controller-manager, scheduler | control plane | Prometheus | Cluster |
| 4240/TCP | Cilium health | all | Agents and health endpoints | Cluster |
| 4244/TCP | Hubble peer | all | Hubble Relay | Cluster |
| 9100/TCP | node-exporter | all | Prometheus | Cluster |
| 9962/TCP, 9965/TCP | Cilium agent and Hubble metrics | all | Prometheus | Cluster |
| 9963/TCP | cilium-operator metrics | The two operator nodes, moves with the pod | Prometheus | Cluster |
| 9964/TCP | Envoy metrics | all | Nothing; the Prometheus egress policy does not name it | Cluster |
| 2112/TCP | Tetragon metrics | all | Prometheus | Cluster |
| 6789/TCP | Tetragon | all | Nothing | Cluster |
| 8472/UDP | VXLAN | all | Other nodes | Cluster |
| 51871/UDP | WireGuard | all | Other nodes | Cluster |
| 68/UDP | DHCP client | all | Router | LAN |
| ICMP echo | Kernel | all | Not measured | LAN, cluster |

## Not filtered, or not listening

| Traffic | Why no rule |
| --- | --- |
| Loopback: 2379, 2381, 10248, 9878, 9879, 9890, 8118, 30003, DNS on 127.0.0.53 and .54, Envoy's and containerd's dynamic ports | Loopback never crosses a device the firewall is attached to |
| NodePorts of the two Gateway Services | Translated to the backend before the firewall |
| The two Gateway addresses | Not node addresses; the firewall checks only packets addressed to one |
| BGP | The agent dials the router and nothing listens on 179, so only replies arrive |
| Ceph mon and OSD ports | Rook runs them on the pod network; the only host-networked Ceph pod, the RBD node plugin, listens on nothing |
| DHCP server, TFTP, PXE | The boot server is off-cluster; a node's PXE boot happens before any agent runs |
| kube-vip | ARP mode: gratuitous ARP passes the firewall, and leader election goes through the API server |

## Findings

| Finding | Consequence |
| --- | --- |
| Services bound to `*` answer on each node's global IPv6 address: `curl` on port 9100 from the workstation returned `200` | The firewall cannot filter IPv6 with `enable-ipv6: false`; it [drops it all](../platform/cilium.md#pitfalls). No IPv6 connection other than loopback was established on any node; each node's second resolver is IPv6, used only after the first fails |
| Prometheus's scrapes arrive from its node's address, not the pod's | Masquerading, see [Host firewall](../platform/security-policies.md#host-firewall). Prometheus-only is held by the pod egress policies: besides the API server rules, only Prometheus, Hubble Relay and metrics-server name a node as a destination, each with its ports |
| Tetragon holds 2112 on the control planes, the port kube-vip also wants | kube-vip logs `bind: address already in use`, and its liveness probe is answered by Tetragon |
| 6789 is Tetragon, not a Ceph monitor | Nothing on a node needs a Ceph rule |
