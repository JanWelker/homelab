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

## OpenBao needs an operator to unseal it

The seal is Shamir, with no auto-unseal configured, so every OpenBao pod comes
back sealed after any restart — a node reboot, a Kured cycle, a chart bump — and
stays that way until someone supplies 3 of the 5 key shares to each of the three
replicas. While it is sealed no `ExternalSecret` resolves and cert-manager
cannot renew certificates.

Read that with a power cut in mind: the house comes back, the cluster comes
back, and the secrets do not, because nobody has typed in the keys yet. See
[Unsealing after a restart](../platform/openbao.md#unsealing-after-a-restart).

The failure is quiet, which is the part that bites. A cluster with OpenBao
sealed looks entirely healthy, and the consequence surfaces sixty days later
when a certificate expires. Checking `bao status` after every reboot is the
[routine](../operations/index.md#after-any-node-reboot-unseal-openbao) that
catches it.

An auto-unseal seal — a cloud KMS, or a transit seal against a second OpenBao —
would remove the manual step, at the price of making something outside the
cluster a hard dependency of it starting up. That trade was made deliberately in
the other direction: the key shares stay entirely in the operator's hands.

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

Reprovisioning any node means a boot server on the nodes' L2 segment, with the
external DHCP server pointing at it. Two machines can be that boot server: the
external boot host, and the cluster itself — the Deployment in
`payload/platform/boot-server/` runs the same image on a control-plane node. See
[In-Cluster Boot Server](../boot_server/in-cluster.md).

So a single dead node can now be rebuilt from a hotel room, as long as the rest
of the cluster is up and the dead node is not the one the boot server is pinned
to. The two cases that matter most are unchanged: a cluster that is down cannot
reprovision anything, and a first build has nothing to run the server on. Both
need a machine physically on the segment.

Plan holidays accordingly — just fewer of them.

## Single-region, single-site, single-rack

There is no failure domain larger than a node. Ceph replicates across nodes in
one rack on one power feed; a site-level event takes everything.

The cluster's true availability zone is "this building has electricity", and no
amount of replication factor changes that. This is the honest limit of a
homelab, and pretending otherwise is how people end up genuinely surprised by a
tripped breaker.
