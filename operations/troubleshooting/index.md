# Troubleshooting

The thing that looks broken is usually downstream of something duller. Start with the table.

## Where to look first

| Symptom | First check |
| --- | --- |
| Secrets missing, certificates not renewing | OpenBao sealed — `kubectl -n openbao exec openbao-0 -- bao status`; if so, `make bao-unseal` |
| An Application is Degraded, OutOfSync or stuck Progressing | `argocd app get <app>` for the failing resource; a failed sync [retries itself for about forty minutes](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>), after that `argocd app sync <app>` — see [ArgoCD](<https://homelab.wlkr.ch/platform/argocd/index.md#health-check>) |
| A node is down | [Rebooting a node](<https://homelab.wlkr.ch/operations/nodes/index.md#rebooting-a-node>); if it does not come back, [Replacing a failed node](<https://homelab.wlkr.ch/operations/nodes/index.md#replacing-a-failed-node>) |
| Pods pending on a fresh node | Node still `NotReady`; check Cilium is running on it |
| `ExternalSecret` not syncing | [External Secrets troubleshooting](<https://homelab.wlkr.ch/platform/external-secrets/index.md#pitfalls>) |
| PVCs stuck pending | `ceph status`, then the [Rook dashboard](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) |
| A hostname stopped resolving to the cluster | Gateway lost its LoadBalancer IP; check the Cilium L2 pool |
| Node not rejoining after reboot | `journalctl -u kubelet` on the node |
| A node will not install, or hangs at a blinking cursor | [Troubleshooting PXE boot](<https://homelab.wlkr.ch/quickstart/index.md#troubleshooting-pxe-boot>) |
| A node installs but comes up with no OSD | [Rook-Ceph → No OSDs after reprovisioning](<https://homelab.wlkr.ch/platform/rook-ceph/index.md#no-osds-after-reprovisioning>) |

Alerts are mailed by Alertmanager — see [Monitoring → Alerting](<https://homelab.wlkr.ch/platform/monitoring/index.md#alerting>) — but the delivery path itself is not monitored, so an empty inbox does not prove anything.

## Rolling back a bad sync

The only fix is a revert commit. Every Application runs with automated sync, so ArgoCD refuses `argocd app rollback`, and a manual sync to an older revision is undone within one polling interval. Do not patch a generated Application's `spec` to switch automated sync off either; the next ApplicationSet reconcile copies the file back over it — see [Sync policy](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>). The exception is `argocd` itself, which is not generated: `argocd app set argocd --sync-policy none` holds a rollback until `syncPolicy.automated` is restored.

> [!WARNING]
>
> Do not fix a broken workload with `kubectl edit`. `selfHeal` on every Application ([Sync policy](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>)) puts the manifest back on the next reconcile, so the fix lasts minutes and leaves no trace of what it was. Change the manifest instead.
