# Operations

Everything here assumes a cluster that is already up; for first provisioning see the [Quickstart](<https://homelab.wlkr.ch/quickstart/index.md>).

- **[Troubleshooting](<https://homelab.wlkr.ch/operations/troubleshooting/index.md>)**

  ---

  The symptom-to-first-check table, and how to roll back a sync that made things worse.
- **[Node Lifecycle](<https://homelab.wlkr.ch/operations/nodes/index.md>)**

  ---

  Rebooting, adding, replacing and rebuilding a node, including the two gates people skip: waiting for Ceph, and unsealing OpenBao afterwards.
- **[Updates &amp; Upgrades](<https://homelab.wlkr.ch/operations/upgrades/index.md>)**

  ---

  The three mechanisms that update a node, and how to move a minor version deliberately.
- **[Backups &amp; Recovery](<https://homelab.wlkr.ch/operations/backups/index.md>)**

  ---

  What is backed up, what is not, and the restore runbooks.
- **[Control Plane VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>)**

  ---

  How the API server address survives losing the node that answers it, and how to migrate a cluster built without it.
- **[Encryption Key Rotation](<https://homelab.wlkr.ch/operations/encryption-key-rotation/index.md>)**

  ---

  Swapping the key that encrypts Secrets in etcd, with no API server restart and two rewrites of every Secret.
- **[Vulnerability Triage](<https://homelab.wlkr.ch/operations/vulnerabilities/index.md>)**

  ---

  How to read the Trivy findings and when to file upstream; the dated logs are [Triage 2026-09-20](<https://homelab.wlkr.ch/operations/triage-2026-09-20/index.md>) and [Triage 2026-09-29](<https://homelab.wlkr.ch/operations/triage-2026-09-29/index.md>).

## Routine health check

| Command | Healthy looks like |
| --- | --- |
| `kubectl get nodes` | Every node `Ready` |
| `kubectl -n argocd get applications` | Every Application `Synced` *and* `Healthy` |
| `kubectl get certificate -A` | Every certificate `READY=True` |
| `ssh <control plane> sudo kubeadm certs check-expiration` | More than thirty days left on every line; less and the [yearly renewal](<https://homelab.wlkr.ch/operations/upgrades/#control-plane-certificates>) is due |
| `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status` | `HEALTH_OK`, and an OSD count matching your node count |
| `kubectl -n openbao exec openbao-0 -- bao status` | `Sealed: false`; the one failure that looks like health, so run it after anything restarted |

The Rook toolbox pod is enabled, so `ceph status`, `ceph osd tree` and `ceph health detail` need nothing installed. Ceph metrics are scraped and Ceph's own alerting rules are loaded, so a degraded pool reaches Prometheus without anyone running `ceph status`; run it anyway, because it is the fastest way to see *why* — see [Rook-Ceph → Monitoring](<https://homelab.wlkr.ch/platform/rook-ceph/#monitoring>).
