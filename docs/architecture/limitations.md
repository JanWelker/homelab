---
description: "Known limitations of this cluster: what is single-homed, unmonitored, unenforced, or unbacked."
---

# Known Limitations

Things this cluster does not currently do, collected in one place so they are
findable before they are discovered. None of these is a bug; each is either an
accepted tradeoff or work not yet done.

Every system has a page like this. Most of them are unwritten, which is why the
same three failure modes keep surprising the same teams. Writing it down does
not fix anything — it just means that when one of these bites, the response is
"ah, that one" rather than four hours of confused archaeology.

## Single API server endpoint

Provisioning places a [kube-vip](https://kube-vip.io/) virtual IP in front of
the API servers, so a cluster built by this repo reaches the control plane at an
address no single machine owns. See
[Control Plane VIP](../operations/control-plane-vip.md).

The endpoint is fixed at `kubeadm init`, though: whatever `controlPlaneEndpoint`
named at bootstrap is baked into the API server certificates, and no amount of
syncing changes it afterwards. A cluster whose certificates name a
control-plane node rather than the VIP is single-homed on that node, and while
it is down:

- No node can join the cluster.
- Cilium on every other node loses its connection to the API server, because
  with `kube-proxy` replaced it cannot reach the API through a Service.
- `output/kubeconfig` points at an address that is not answering.

The other control-plane nodes keep running and etcd keeps quorum, so existing
workloads continue; it is control-plane *access* that fails. Which is its own
special kind of frustrating: the cluster is fine, you simply cannot talk to it.

`k8sServiceHost` in the Cilium values must name an address that actually
answers — pointing it at one that does not takes the CNI down cluster-wide — so
it moves to the VIP only once the VIP is live. The ordering is in
[Migrating a cluster built without a VIP](../operations/control-plane-vip.md#migrating-a-cluster-built-without-a-vip).

## OpenBao depends on AWS KMS to start

OpenBao auto-unseals against AWS KMS, which makes a service outside the cluster
and outside the house a hard dependency of the cluster starting up. If KMS is
unreachable — key deleted, IAM user disabled, no internet — every OpenBao pod
stays sealed, no `ExternalSecret` resolves, and cert-manager cannot renew
certificates.

Read that sentence again with a power cut in mind: the house comes back, the
cluster comes back, and the secrets do not, because the ISP is still down. See
[Auto-unseal](../platform/openbao.md#auto-unseal).

The 5 shares survive as recovery keys and are still the way out, so this is
recoverable rather than fatal. It is a trade of a frequent, certain manual step
for a rare, external one. Keep the keys somewhere that does not require the
cluster to read.

## Alerting reaches one mailbox

The delivery path out of Alertmanager is single-homed and unmonitored. There is
one receiver, one mailbox, and one SMTP provider; if that provider rejects mail
or the password expires, alerts stop and nothing says so. `Watchdog` proves the
pipeline as far as Alertmanager, not as far as the inbox.

This is the oldest failure in monitoring: the thing that tells you when things
break, breaking quietly. Silence is not evidence of health. See
[Alerting](../platform/monitoring.md#alerting).

A second receiver on a different transport would fix it. Meanwhile the health
checks in [Operations](../operations/index.md#routine-health-check) remain worth
running.

## Backups do not leave the cluster

Velero backs up Kubernetes objects and PVC data nightly, and a CronJob snapshots
etcd — both into the same Ceph object store the cluster runs on. See
[Backups & Recovery](../operations/backups.md).

Writing them next to their source protects against the failures that actually
happen — a deleted PVC, a bad `prune`, a workload that ate its own data — and
not at all against losing the cluster. A backup that shares a failure domain
with its source is a convenience feature, not a backup, and it is worth being
honest about which one you have.

RGW bucket replication or a second Velero `BackupStorageLocation` would close
it; neither is configured. Until then Git plus the OpenBao unseal keys is the
real disaster-recovery story, and these backups protect against mistakes rather
than against the building burning down.

## Automatic updates stop at patch releases

Sysupdate configs are pinned to the Kubernetes and containerd major.minor in
`ansible/inventory.yaml`, so a node picks up patch releases inside its series
and cannot stage a minor kubeadm refuses to skip to. See
[Nodes are pinned to a minor series](../operations/upgrades.md#nodes-are-pinned-to-a-minor-series).
[Kured](../platform/kured.md) then drains and reboots one node at a time
between 01:00 and 05:00 to apply what has been staged, and refuses while Ceph
or etcd is unhealthy.

Two things follow that are worth knowing:

- **A minor Kubernetes upgrade is manual.** Kured applies whatever the sysext
  already staged, and the pin means that is only ever a patch. Moving to a new
  minor means changing the inventory, pushing the new sysupdate config to
  running nodes, and running `kubeadm upgrade` — see
  [Upgrading a minor version deliberately](../operations/upgrades.md#upgrading-a-minor-version-deliberately).
- **The alert list is a judgement call.** Kured blocks on the Ceph, etcd and
  node-readiness alerts named in its config. An alert outside that list will not
  stop a reboot.

## Network policy is partial, and AppProjects are permissive

Four namespaces have default-deny ingress and every platform namespace has Pod
Security Admission labels — see
[Security Policies](../platform/security-policies.md).

Beyond that: all **egress** is unrestricted everywhere, and the namespaces
outside those four allow all ingress. Two of the three ArgoCD AppProjects allow
every resource kind in every namespace. Details in
[Security Posture](security.md#authorization).

Partial network policy is genuinely better than none, but it is worth not
mistaking it for a boundary. A pod in `authentik` can still talk to a pod in
`kured` all day long.

## Provisioning requires the boot server on the same segment

Reprovisioning any node means running `make serve` on a machine on the nodes' L2
segment, with the external DHCP server pointing at it. There is no way to
rebuild a node remotely, and the deployment host is not part of the cluster.

Translation: you cannot fix a dead node from a hotel room. Plan holidays
accordingly.

## Single-region, single-site, single-rack

There is no failure domain larger than a node. Ceph replicates across nodes in
one rack on one power feed; a site-level event takes everything.

The cluster's true availability zone is "this building has electricity", and no
amount of replication factor changes that. This is the honest limit of a
homelab, and pretending otherwise is how people end up genuinely surprised by a
tripped breaker.
