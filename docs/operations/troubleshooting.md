---
description: "Where to look first when something is wrong, and how to roll back a sync that made it worse."
---

# Troubleshooting

The thing that looks broken is usually downstream of something duller. Start
with the table.

## Where to look first

| Symptom | First check |
| --- | --- |
| Secrets missing, certificates not renewing | OpenBao sealed — `kubectl -n openbao exec openbao-0 -- bao status`; if so, `make bao-unseal` |
| An Application is Degraded, OutOfSync or stuck Progressing | `argocd app get <app>` for the failing resource; a failed sync [retries itself for about forty minutes](../architecture/gitops.md#sync-policy), after that `argocd app sync <app>` — see [ArgoCD](../platform/argocd.md#health-check) |
| A node is down | [Rebooting a node](nodes.md#rebooting-a-node); if it does not come back, [Replacing a failed node](nodes.md#replacing-a-failed-node) |
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

The only fix is a revert commit. Every Application runs with automated sync,
so ArgoCD refuses `argocd app rollback`, and a manual sync to an older revision
is undone within one polling interval. Do not patch a generated Application's
`spec` to switch automated sync off either; the next ApplicationSet reconcile
copies the file back over it — see
[Sync policy](../architecture/gitops.md#sync-policy). The exception is
`argocd` itself, which is not generated: `argocd app set argocd --sync-policy
none` holds a rollback until `syncPolicy.automated` is restored.

!!! warning
    Do not fix a broken workload with `kubectl edit`. Under the ApplicationSet nothing reverts the edit, so the cluster quietly stops matching Git and the next sync of that Application undoes your fix without warning. Change the manifest instead.
