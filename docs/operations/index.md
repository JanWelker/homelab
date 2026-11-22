---
description: "Running the cluster day to day: the routine health check, and where the runbooks for everything else live."
---

# Operations

Everything here assumes a cluster that is already up; for first provisioning
see the [Quickstart](../quickstart.md).

<div class="grid cards" markdown>

- **[Troubleshooting](troubleshooting.md)**

    ---

    The symptom-to-first-check table, and how to roll back a sync that made
    things worse.

- **[Node Lifecycle](nodes.md)**

    ---

    Rebooting, adding, replacing and rebuilding a node, and recovering from a
    power loss.

- **[Updates & Upgrades](upgrades.md)**

    ---

    The three mechanisms that update a node, and how to move a minor version
    deliberately.

- **[Backups & Recovery](backups.md)**

    ---

    What is backed up, what is not, and the restore runbooks.

- **[Control Plane VIP](control-plane-vip.md)**

    ---

    How the API server address survives losing the node that answers it, and
    how to migrate a cluster built without it.

- **[Encryption Key Rotation](encryption-key-rotation.md)**

    ---

    Swapping the key that encrypts Secrets in etcd.

- **[Vulnerability Triage](vulnerabilities.md)**

    ---

    How to read the Trivy findings and when to file upstream; logs:
    [2026-09-20](triage-2026-09-20.md), [2026-09-29](triage-2026-09-29.md),
    [2026-11-21](triage-2026-11-21.md).

</div>

Other dated logs: network-policy audits
[2026-09-27](network-policy-audit-2026-09-27.md) and
[2026-10-16](network-policy-audit-2026-10-16.md), host firewall
[inventory](host-firewall-inventory-2026-10-30.md) and
[rollout](host-firewall-rollout-2026-11-18.md).

## Routine health check

| Command | Healthy looks like |
| --- | --- |
| `kubectl get nodes` | Every node `Ready` |
| `kubectl -n argocd get applications` | Every Application `Synced` *and* `Healthy` |
| `kubectl get certificate -A` | Every certificate `READY=True` |
| `ssh core@<control plane> sudo kubeadm certs check-expiration` | More than thirty days left on every line; less and the [yearly renewal](upgrades.md#control-plane-certificates) is due |
| `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status` | `HEALTH_OK`, and an OSD count matching your node count |
| `bao status` on [every OpenBao pod](../platform/openbao.md#health-check), each seals on its own | `Sealed: false`; the one failure that looks like health, so run it after anything restarted |

The Rook toolbox pod is enabled, so `ceph status`, `ceph osd tree` and
`ceph health detail` need nothing installed. Ceph metrics are scraped and
Ceph's own alerting rules are loaded, so a degraded pool reaches Prometheus
without anyone running `ceph status`; run it anyway, because it is the fastest
way to see *why* — see [Rook-Ceph &rarr; Monitoring](../platform/rook-ceph.md#monitoring).
