---
description: "Kured drains and reboots nodes to apply staged OS, Kubernetes and containerd updates, one at a time and only when Ceph and etcd are healthy."
---

# Kured

[Kured](https://kured.dev/) is what finally applies the updates this cluster has
been staging. It watches every node for a sentinel file, and when it finds one:
takes a cluster-wide lock, cordons the node, drains it, reboots it, waits for it
to come back, and uncordons it.

## The gap it closes

Three mechanisms update a node, and all three used to stop at the same place —
changes landed on disk and waited for a human:

| Mechanism | Stages | Signals by |
| --- | --- | --- |
| Flatcar OS (update-engine) | New image on the passive A/B partition | `flatcar-reboot-sentinel.timer` touching `/run/reboot-required` |
| Kubernetes sysext | New `.raw` under `/opt/extensions/kubernetes` | `systemd-sysupdate` drop-in touching the same file |
| containerd sysext | New `.raw` under `/opt/extensions/containerd` | Same |

`locksmithd` — the Flatcar daemon that would normally coordinate reboots — is
masked, because it reboots without draining. Kured replaces it and drains first.

All three paths converge on `/run/reboot-required`, which is Kured's default
sentinel. `/run` is tmpfs, so the marker clears itself on reboot; nothing has to
remember to delete it.

## Configuration

| Setting | Value | Why |
| --- | --- | --- |
| Window | 01:00–05:00, `Europe/Berlin` | Reboots happen while nobody is using the cluster |
| Check period | 30m | |
| Concurrency | 1 | One node down at a time, never two |
| `lockReleaseDelay` | 10m | Breathing room between nodes for Ceph to backfill |
| `drainTimeout` | 15m | |
| `forceReboot` | `false` | A node that will not drain is a node worth looking at |
| `preferNoScheduleTaint` | `weave.works/kured-node-reboot` | A node pending reboot stops attracting pods about to be evicted again |

### Asking Prometheus first

The important guard is not the delay, it is the alert check. Kured queries
Prometheus before taking a node down and refuses if anything matching this fires:

```text
CephClusterErrorState, CephClusterWarningState, CephOSDDown, CephPGsUnhealthy,
CephPGsDegraded, CephPGsNotDeepScrubbed, CephMonQuorumAtRisk, CephMonQuorumLost,
CephNodeDown, etcdMembersDown, etcdInsufficientMembers, etcdNoLeader,
KubeNodeNotReady, KubeAPIDown
```

This is the automated form of the "confirm Ceph has recovered before moving on"
step in [Rebooting a node](../operations/index.md#rebooting-a-node): rebooting a
second node while a placement group is still backfilling can take it below its
minimum replica count.

!!! note "The regex means the opposite of what it looks like"
    `alertFilterRegexp` normally lists alerts to **ignore**. With
    `alertFilterMatchOnly: true` the meaning inverts: these become the only
    alerts that block. That is deliberate. Something is almost always firing in
    a homelab, and blocking on *any* alert would mean never rebooting at all —
    which is exactly the state this component exists to fix. The flip side is
    that an alert not on this list will not stop a reboot: the list is the
    judgement call, so widen it if something turns out to matter.

## Watching it work

```bash
# Which nodes want a reboot
kubectl get nodes -o json | jq -r '.items[] | select(.metadata.annotations["weave.works/kured-reboot-in-progress"]) | .metadata.name'

# Whether a node has staged anything
ssh core@<node> 'ls -l /run/reboot-required; update_engine_client -status'

kubectl -n kured logs -l app.kubernetes.io/name=kured --tail=50
```

To stop Kured rebooting anything without uninstalling it, remove the sentinel or
scale the DaemonSet to zero. To force a node to reboot on the next pass:

```bash
ssh core@<node> sudo touch /run/reboot-required
```

## Directory Structure

```text
kured/
└── application.yaml   # ArgoCD Application (Helm: kured)
```
