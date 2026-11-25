# Troubleshooting

## Where to look first

| Symptom | First check |
| --- | --- |
| Secrets missing, certificates not renewing | OpenBao sealed: `bao status` on [every pod](<https://homelab.wlkr.ch/platform/openbao/index.md#health-check>); if so, [unseal](<https://homelab.wlkr.ch/platform/openbao/index.md#unsealing-after-a-restart>) |
| An Application is Degraded, OutOfSync or stuck Progressing | `argocd app get <app>` ([login](<https://homelab.wlkr.ch/platform/argocd/index.md#cli-access>)), or `kubectl -n argocd get application <app> -o yaml` with Authentik down; a failed sync [retries itself](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>), then needs `argocd app sync <app>` |
| Cannot log in anywhere | [When Authentik is down](<https://homelab.wlkr.ch/platform/authentik/index.md#when-authentik-is-down>) |
| `kubectl` times out, API unreachable | `kubectl --server https://<control plane>:6443`, then [who holds the VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md#verifying>) |
| `kubectl`: certificate expired | [Control-plane certificates](<https://homelab.wlkr.ch/operations/upgrades/index.md#control-plane-certificates>) |
| A node down or not rejoining | `ssh core@<node> journalctl -u kubelet`; dead for good: [Replacing a failed node](<https://homelab.wlkr.ch/operations/nodes/index.md#replacing-a-failed-node>) |
| Node unreachable after a host firewall change | [Host firewall lockout](<https://homelab.wlkr.ch/platform/security-policies/index.md#host-firewall-lockout>) |
| `ExternalSecret` not syncing | [External Secrets troubleshooting](<https://homelab.wlkr.ch/platform/external-secrets/index.md#pitfalls>) |
| PVCs stuck pending | `ceph status`, then the [Rook dashboard](<https://homelab.wlkr.ch/platform/rook-ceph/index.md>) |
| Data deleted, or etcd lost quorum | [Restoring](<https://homelab.wlkr.ch/operations/backups/index.md#restoring>), [etcd restore](<https://homelab.wlkr.ch/operations/backups/index.md#etcd-restore>) |
| Velero backups failing, repositories NotReady | [Velero recovery](<https://homelab.wlkr.ch/platform/velero/index.md#recovery>) |
| The etcd encryption key leaked | [Encryption Key Rotation](<https://homelab.wlkr.ch/operations/encryption-key-rotation/index.md>) |
| A hostname stopped resolving to the cluster | Gateway lost its LoadBalancer IP; check the Cilium L2 pool |
| A node will not install, or hangs at a blinking cursor | [Troubleshooting PXE boot](<https://homelab.wlkr.ch/quickstart/index.md#troubleshooting-pxe-boot>) |
| A node installs but comes up with no OSD | [No OSDs after reprovisioning](<https://homelab.wlkr.ch/platform/rook-ceph/index.md#no-osds-after-reprovisioning>) |

Alerts are [mailed by Alertmanager](<https://homelab.wlkr.ch/platform/monitoring/index.md#alerting>), but nothing monitors the delivery path, so an empty inbox proves nothing.

## Rolling back a bad sync

The only fix is a revert commit. Under automated sync ArgoCD refuses `argocd app rollback`, a manual sync to an older revision is undone at the next poll, and a patched generated Application is overwritten by the next ApplicationSet reconcile — see [Sync policy](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>). The exception is `argocd` itself, which is not generated: `argocd app set argocd --sync-policy none` holds a rollback until `syncPolicy.automated` is restored.

> [!WARNING]
>
> Do not fix a broken workload with `kubectl edit`: `selfHeal` puts the manifest back on the next reconcile, leaving no trace of the fix. Change the manifest instead.
