---
description: "Running the cluster day to day: the routine health check, and where the runbooks for everything else live."
---

# Operations

Everything in this section assumes a cluster that is already up. For first
provisioning see the [Quickstart](../quickstart.md).

Building a cluster is a weekend. Operating one is the rest of your life. This is
the section you will actually come back to.

## What is in this section

<div class="grid cards" markdown>

- **[Troubleshooting](troubleshooting.md)**

    ---

    The symptom-to-first-check table, and how to roll back a sync that made
    things worse. Start here when something is wrong.

- **[Node Lifecycle](nodes.md)**

    ---

    Rebooting, adding, replacing and rebuilding a node — including the two gates
    people skip: waiting for Ceph, and unsealing OpenBao afterwards.

- **[Updates & Upgrades](upgrades.md)**

    ---

    The three mechanisms that update a node, why none of them finishes on its
    own, and how to move a minor version deliberately.

- **[Backups & Recovery](backups.md)**

    ---

    What is backed up, what is not, and the fact worth knowing before you need
    it: every automated backup lands inside the cluster it is backing up.

- **[Control Plane VIP](control-plane-vip.md)**

    ---

    How the API server address survives losing the node that answers it, and how
    to migrate a cluster that was built without it.

</div>

## Routine health check

Four commands, thirty seconds, run them when you walk past the rack. The second
column is what you are actually looking for, because three of the four will
happily print something reassuring while being wrong:

| Command | Healthy looks like |
| --- | --- |
| `kubectl get nodes` | Every node `Ready` |
| `kubectl -n argocd get applications` | Every Application `Synced` *and* `Healthy` |
| `kubectl get certificate -A` | Every certificate `READY=True` |
| `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status` | `HEALTH_OK`, and an OSD count matching your node count |

A fifth is worth adding after anything restarted, because it is the one failure
that looks like health:

```bash
kubectl -n openbao exec openbao-0 -- bao status   # Sealed: false
```

The Rook toolbox pod is enabled, so `ceph status`, `ceph osd tree` and
`ceph health detail` are available without installing anything.

!!! note
    Ceph metrics **are** scraped — `monitoring.enabled` is `true` in the `CephCluster` spec, and `createPrometheusRules` ships Ceph's own alerting rules — so a degraded pool or a down OSD reaches Prometheus without anyone running `ceph status`. Run it anyway: it is the fastest way to see *why*, and it is what [Kured](../platform/kured.md) is really asking about before it reboots anything. See [Rook-Ceph &rarr; Monitoring](../platform/rook-ceph.md#monitoring).
