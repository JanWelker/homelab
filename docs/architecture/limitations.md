---
description: "Known limitations of this cluster: what is single-homed, unmonitored, unenforced, or unbacked."
---

# Known Limitations

Things this cluster does not currently do, collected in one place so they are
findable before they are discovered. None of these is a bug; each is either an
accepted tradeoff or work not yet done.

## Single API server endpoint

**Fixed for newly provisioned clusters.** Provisioning now places a [kube-vip](https://kube-vip.io/) virtual IP in front
of the API servers, so `controlPlaneEndpoint` no longer names one machine. See
[Control Plane VIP](../operations/control-plane-vip.md).

**A cluster provisioned before that change is not fixed by merging it.** The
first control-plane node's address is baked into the API server certificates at
`kubeadm init`, so moving to the VIP is a migration, not a sync. Until it is
done, if that node is down:

- No node can join the cluster.
- Cilium on every other node loses its connection to the API server, because
  with `kube-proxy` replaced it cannot reach the API through a Service.
- `output/kubeconfig` points at an address that is no longer answering.

The other control-plane nodes keep running and etcd keeps quorum, so existing
workloads continue; it is control-plane *access* that fails.

`k8sServiceHost` in the Cilium values still names the first control-plane node
and must stay that way until the VIP answers — pointing it at an address that
does not respond takes the CNI down cluster-wide. The ordering is in
[Migrating a cluster built without a VIP](../operations/control-plane-vip.md#migrating-a-cluster-built-without-a-vip).

## OpenBao must be unsealed by hand

Auto-unseal is not configured, so OpenBao seals on every pod restart and stays
sealed until an operator supplies 3 of the 5 unseal keys. While it is sealed no
`ExternalSecret` resolves, which means cert-manager cannot renew certificates.

A power cut therefore brings the cluster back into a state where it is running
but cannot issue certificates until a human intervenes. See
[Unsealing after a restart](../platform/openbao.md#unsealing-after-a-restart).

## Alerting reaches one mailbox

**Fixed.** Alertmanager routes to an email receiver, and Ceph is scraped, so
storage health reaches Prometheus. See
[Alerting](../platform/monitoring.md#alerting).

What is left is that the delivery path is single-homed and unmonitored. There is
one receiver, one mailbox, and one SMTP provider; if that provider rejects mail
or the password expires, alerts stop and nothing says so. `Watchdog` proves the
pipeline as far as Alertmanager, not as far as the inbox.

A second receiver on a different transport would fix it. Meanwhile the health
checks in [Operations](../operations/index.md#routine-health-check) remain worth
running.

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
