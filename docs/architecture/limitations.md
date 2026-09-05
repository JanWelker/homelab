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

## OpenBao depends on AWS KMS to start

**Fixed, by taking on a different dependency.** OpenBao auto-unseals against
AWS KMS, so a pod restart no longer needs an operator with 3 of 5 key shares,
and a power cut no longer leaves the cluster unable to renew certificates. See
[Auto-unseal](../platform/openbao.md#auto-unseal).

What replaces it is a hard dependency on something outside the cluster and
outside the house. If KMS is unreachable — key deleted, IAM user disabled, no
internet — every OpenBao pod stays sealed, no `ExternalSecret` resolves, and
cert-manager cannot renew certificates. That is the same failure mode as before,
now triggered by an external service rather than by a reboot.

The 5 shares survive as recovery keys and are still the way out, so this is
recoverable rather than fatal. It is a trade of a frequent, certain manual step
for a rare, external one.

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

## Backups do not leave the cluster

**Mostly fixed.** Velero backs up Kubernetes objects and PVC data nightly, and a
CronJob snapshots etcd, both into the Ceph object store. See
[Backups & Recovery](../operations/backups.md).

What remains is where they land. Every automated backup is written to the same
Ceph cluster it was taken from, which protects against the failures that
actually happen — a deleted PVC, a bad `prune`, a workload that ate its own data
— and not at all against losing the cluster.

RGW bucket replication or a second Velero `BackupStorageLocation` would close
it; neither is configured. Until then Git plus the OpenBao unseal keys is the
real disaster-recovery story, and these backups protect against mistakes rather
than against the building burning down.

## Updates apply themselves, within a window

**Fixed.** Sysupdate configs are pinned to the Kubernetes and containerd
major.minor in `ansible/inventory.yaml`, so a node picks up patch releases
inside its series and cannot stage a minor kubeadm refuses to skip to. See
[Nodes are pinned to a minor series](../operations/upgrades.md#nodes-are-pinned-to-a-minor-series).
[Kured](../platform/kured.md) then drains and reboots one node at a time
between 01:00 and 05:00 to apply what has been staged, and refuses while Ceph
or etcd is unhealthy.

Two things follow that are worth knowing:

- **A minor Kubernetes upgrade is still manual.** Kured applies whatever the
  sysext already staged, and the pin means that is only ever a patch. Moving to
  a new minor means changing the inventory, pushing the new sysupdate config to
  running nodes, and running `kubeadm upgrade` — see
  [Upgrading a minor version deliberately](../operations/upgrades.md#upgrading-a-minor-version-deliberately).
- **The alert list is a judgement call.** Kured blocks on the Ceph, etcd and
  node-readiness alerts named in its config. An alert outside that list will not
  stop a reboot.

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
