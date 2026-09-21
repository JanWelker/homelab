---
description: "Where to look first when something is wrong, and how to roll back a sync that made it worse."
---

# Troubleshooting

The thing that looks broken is usually downstream of something duller. Start
with the table.

## Where to look first

| Symptom | First check |
| --- | --- |
| Secrets missing, certificates not renewing | OpenBao sealed — `bao status` |
| Pods pending on a fresh node | Node still `NotReady`; check Cilium is running on it |
| `ExternalSecret` not syncing | [External Secrets troubleshooting](../platform/external-secrets.md#pitfalls) |
| PVCs stuck pending | `ceph status`, then the [Rook dashboard](../platform/rook-ceph.md) |
| A hostname stopped resolving to the cluster | Gateway lost its LoadBalancer IP; check the Cilium L2 pool |
| Node not rejoining after reboot | `journalctl -u kubelet` on the node |
| A node will not install, or hangs at a blinking cursor | [Troubleshooting PXE boot](../quickstart.md#troubleshooting-pxe-boot) |
| A node installs but comes up with no OSD | [Rook-Ceph &rarr; No OSDs after reprovisioning](../platform/rook-ceph.md#no-osds-after-reprovisioning) |

Alerts are mailed by Alertmanager — see
[Monitoring &rarr; Alerting](../platform/monitoring.md#alerting) — but the
delivery path itself is not monitored, so an empty inbox does not prove
anything.

## Rolling back a bad sync

The durable fix is a revert commit. To stop the bleeding first:

```bash
argocd app rollback <app>
```

Nothing has to be disabled: the generated platform Applications have no
automated sync under `RollingSync`, so a rollback holds until that
Application's next change — see
[what the staging costs](../architecture/gitops.md#what-the-staging-costs).
Do not patch their `spec` to pin them; the next reconcile copies the file back
over it. The exception is `argocd` itself, which is not generated and runs with
`automated`: pinning it means restoring `syncPolicy.automated` afterwards.

!!! warning
    Do not fix a broken workload with `kubectl edit`. Under the ApplicationSet nothing reverts the edit, so the cluster quietly stops matching Git and the next sync of that Application undoes your fix without warning. Change the manifest instead.
