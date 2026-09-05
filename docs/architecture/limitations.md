---
description: "Known limitations of this cluster: what is single-homed, unmonitored, unenforced, or unbacked."
---

# Known Limitations

Things this cluster does not currently do, collected in one place so they are
findable before they are discovered. None of these is a bug; each is either an
accepted tradeoff or work not yet done.

## Single API server endpoint

Three control-plane nodes run etcd and the control-plane components, so the
Kubernetes control plane is replicated. **Access to it is not.**

`controlPlaneEndpoint` is generated as the address of the first control-plane
node in the inventory, and `k8sServiceHost` in the Cilium values names the same
address. There is no virtual IP and no load balancer in front of the API
servers. Concretely, if `odin` is down:

- No node can join the cluster.
- Cilium on every other node loses its connection to the API server, because
  with `kube-proxy` replaced it cannot reach the API through a Service.
- `output/kubeconfig` points at an address that is no longer answering.

The other two control-plane nodes keep running and etcd keeps quorum, so
existing workloads continue; it is control-plane *access* that fails.

Fixing this properly means a VIP in front of the three API servers — kube-vip or
keepalived — and pointing `controlPlaneEndpoint` and `k8sServiceHost` at it.
That has to be decided before provisioning, because `controlPlaneEndpoint` is
baked into the cluster's certificates at `kubeadm init`.

## OpenBao must be unsealed by hand

Auto-unseal is not configured, so OpenBao seals on every pod restart and stays
sealed until an operator supplies 3 of the 5 unseal keys. While it is sealed no
`ExternalSecret` resolves, which means cert-manager cannot renew certificates.

A power cut therefore brings the cluster back into a state where it is running
but cannot issue certificates until a human intervenes. See
[Unsealing after a restart](../platform/openbao.md#unsealing-after-a-restart).

## Nothing is alerted on

Alertmanager is deployed with persistent storage but no route and no receiver,
so alerts reach the default null receiver. Ceph is not scraped at all
(`monitoring.enabled` is `false` on the `CephCluster`), so storage health never
reaches Prometheus in the first place.

Until a receiver exists, the health checks in
[Operations](../operations/index.md#routine-health-check) are the only way a
problem is noticed.

## No backups except OpenBao

etcd, Ceph volumes, and the Grafana dashboard PVC have no backup path. The
cluster is reconstructible from Git plus the OpenBao unseal keys; anything
written into it is not. See [Backups & Recovery](../operations/backups.md).

## Updates are staged, never applied

`locksmithd` is masked, so nothing reboots a node to apply a staged OS update.
Separately, the sysupdate configs track the newest Kubernetes and containerd
images published upstream rather than the versions pinned in the inventory, so a
long-running node can stage a minor version that kubeadm does not support
skipping to. See [Updates & Upgrades](../operations/upgrades.md).

## No network policy, and permissive AppProjects

Pod-to-pod traffic is unrestricted, and two of the three ArgoCD AppProjects
allow every resource kind in every namespace. Details and the reasoning in
[Security Posture](security.md#authorization).

## Provisioning requires the boot server on the same segment

Reprovisioning any node means running `make serve` on a machine on the nodes' L2
segment, with the external DHCP server pointing at it. There is no way to
rebuild a node remotely, and the deployment host is not part of the cluster.

## Single-region, single-site, single-rack

There is no failure domain larger than a node. Ceph replicates across nodes in
one rack on one power feed; a site-level event takes everything.
