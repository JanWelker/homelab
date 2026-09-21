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

    Rebooting, adding, replacing and rebuilding a node, including the two
    gates people skip: waiting for Ceph, and unsealing OpenBao afterwards.

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

- **[Vulnerability Triage](vulnerabilities.md)**

    ---

    How to read the Trivy findings and when to file upstream; the dated log is
    in [Triage 2026-09-20](triage-2026-09-20.md).

</div>

## Routine health check

| Command | Healthy looks like |
| --- | --- |
| `kubectl get nodes` | Every node `Ready` |
| `kubectl -n argocd get applications` | Every Application `Synced` *and* `Healthy` |
| `kubectl get certificate -A` | Every certificate `READY=True` |
| `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status` | `HEALTH_OK`, and an OSD count matching your node count |
| `kubectl -n openbao exec openbao-0 -- bao status` | `Sealed: false`; the one failure that looks like health, so run it after anything restarted |

The Rook toolbox pod is enabled, so `ceph status`, `ceph osd tree` and
`ceph health detail` need nothing installed. Ceph metrics are scraped and
Ceph's own alerting rules are loaded, so a degraded pool reaches Prometheus
without anyone running `ceph status`; run it anyway, because it is the fastest
way to see *why* — see [Rook-Ceph &rarr; Monitoring](../platform/rook-ceph.md#monitoring).
