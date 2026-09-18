---
description: "Where to look first when something is wrong, and how to roll back a sync that made it worse."
---

# Troubleshooting

Start here, not with `kubectl describe` on the thing that looks broken. The
thing that looks broken is usually downstream of something duller, and this
section is ordered by how often the duller thing turns out to be the answer.

## Where to look first

The table that saves the most time:

| Symptom | First check |
| --- | --- |
| Secrets missing, certificates not renewing | OpenBao sealed — `bao status` |
| Pods pending on a fresh node | Node still `NotReady`; check Cilium is running on it |
| `ExternalSecret` not syncing | [External Secrets troubleshooting](../platform/external-secrets.md#troubleshooting) |
| PVCs stuck pending | `ceph status`, then the [Rook dashboard](../platform/rook-ceph.md) |
| A hostname stopped resolving to the cluster | Gateway lost its LoadBalancer IP; check the Cilium L2 pool |
| Node not rejoining after reboot | `journalctl -u kubelet` on the node |

Two other symptom tables exist, for the phases this one does not cover:

- A node that will not install, or hangs at a blinking cursor:
  [Troubleshooting PXE boot](../quickstart.md#troubleshooting-pxe-boot).
- A node that installs but comes up with no OSD:
  [Rook-Ceph &rarr; No OSDs after reprovisioning](../platform/rook-ceph.md#no-osds-after-reprovisioning).

Alerts are mailed by Alertmanager — see
[Monitoring &rarr; Alerting](../platform/monitoring.md#alerting). The checks above
are still worth running, because the delivery path itself is not monitored. An
empty inbox means either that nothing is wrong or that the mail stopped working,
and those two look identical from here.

## Rolling back a bad sync

Everything under `payload/` is applied by ArgoCD from Git, so the durable fix is
a revert commit. To stop the bleeding first, roll the Application back:

```bash
argocd app rollback <app>
```

Nothing has to be disabled first. The Applications the `platform` ApplicationSet
generates already have automated sync switched off — the ApplicationSet
controller syncs them — so a rollback holds until the next change to that
Application. Do not patch their `spec` to pin them: the ApplicationSet copies
`spec` wholesale from `payload/platform/*/application.yaml` on its next
reconcile and the patch disappears.

The exception is `argocd` itself, which is not generated and does run with
`automated`. Pinning that one means restoring `syncPolicy.automated` afterwards,
and an Application that has quietly stopped tracking Git is a time bomb with a
three-month fuse, defused only by somebody wondering why their change never took
effect.

!!! warning
    Do not fix a broken workload by editing live objects with `kubectl edit`. Under the ApplicationSet these edits are worse than useless rather than merely useless: nothing reverts them, so the cluster quietly stops matching Git and the next sync of that Application — whenever its revision happens to change — undoes your fix without warning. Change the manifest instead.
