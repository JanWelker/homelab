---
description: "Where to look first when something is wrong, and how to roll back a sync that made it worse."
---

# Troubleshooting

## Where to look first

| Symptom | First check |
| --- | --- |
| Secrets missing, certificates not renewing | OpenBao sealed: `bao status` on [every pod](../platform/openbao.md#health-check); if so, [unseal](../platform/openbao.md#unsealing-after-a-restart) |
| An Application is Degraded, OutOfSync or stuck Progressing | `argocd app get <app>` ([login](../platform/argocd.md#cli-access)), or `kubectl -n argocd get application <app> -o yaml` with Authentik down; a failed sync [retries itself](../architecture/gitops.md#sync-policy), then needs `argocd app sync <app>` |
| Cannot log in anywhere | [When Authentik is down](../platform/authentik.md#when-authentik-is-down) |
| `kubectl` times out, API unreachable | `kubectl --server https://<control plane>:6443`, then [who holds the VIP](control-plane-vip.md#verifying) |
| `kubectl`: certificate expired | [Control-plane certificates](upgrades.md#control-plane-certificates) |
| A node down or not rejoining | `ssh core@<node> journalctl -u kubelet`; dead for good: [Replacing a failed node](nodes.md#replacing-a-failed-node) |
| Node unreachable after a host firewall change | [Host firewall lockout](../platform/security-policies.md#host-firewall-lockout) |
| `ExternalSecret` not syncing | [External Secrets troubleshooting](../platform/external-secrets.md#pitfalls) |
| PVCs stuck pending | `ceph status`, then the [Rook dashboard](../platform/rook-ceph.md) |
| Data deleted, or etcd lost quorum | [Restoring](backups.md#restoring), [etcd restore](backups.md#etcd-restore) |
| Velero backups failing, repositories NotReady | [Velero recovery](../platform/velero.md#recovery) |
| The etcd encryption key leaked | [Encryption Key Rotation](encryption-key-rotation.md) |
| A hostname stopped resolving to the cluster | Gateway lost its LoadBalancer IP; check the Cilium L2 pool |
| A node will not install, or hangs at a blinking cursor | [Troubleshooting PXE boot](../quickstart.md#troubleshooting-pxe-boot) |
| A node installs but comes up with no OSD | [No OSDs after reprovisioning](../platform/rook-ceph.md#no-osds-after-reprovisioning) |

Alerts are [mailed by Alertmanager](../platform/monitoring.md#alerting), but
nothing monitors the delivery path, so an empty inbox proves nothing.

## Rolling back a bad sync

The only fix is a revert commit. Under automated sync ArgoCD refuses
`argocd app rollback`, a manual sync to an older revision is undone at the
next poll, and a patched generated Application is overwritten by the next
ApplicationSet reconcile — see
[Sync policy](../architecture/gitops.md#sync-policy). The exception is
`argocd` itself, which is not generated: `argocd app set argocd --sync-policy
none` holds a rollback until `syncPolicy.automated` is restored.

!!! warning
    Do not fix a broken workload with `kubectl edit`: `selfHeal` puts the manifest back on the next reconcile, leaving no trace of the fix. Change the manifest instead.
