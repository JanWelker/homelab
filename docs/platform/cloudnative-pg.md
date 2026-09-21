---
description: "The PostgreSQL operator every workload database runs on, why a database is never a subchart here, and what the operator does that a StatefulSet does not."
---

# CloudNativePG

Every chart that needs PostgreSQL ships its own, each upgraded by whoever
maintains the chart that buried it. CloudNativePG is one operator in
`cnpg-system` and a `Cluster` resource per database.

## At a glance

| | |
| --- | --- |
| Namespace | `cnpg-system` for the operator; each database in its workload's namespace |
| Depends on | [Rook-Ceph](rook-ceph.md) for the database volumes |
| If it is down | Running databases keep running; failover, upgrades and new clusters stop |
| Health check | `kubectl get clusters.postgresql.cnpg.io -A` &rarr; `Cluster in healthy state` |
| Files | `payload/platform/cloudnative-pg/` |

## Configuration

A `Cluster` is not a StatefulSet with a nicer name:

| It handles | Instead of |
| --- | --- |
| Primary election and failover between instances | A single pod that takes the database down with it |
| `-rw` and `-ro` Services that follow the primary | Hardcoding a pod name and being wrong after a restart |
| Rolling minor-version upgrades, replicas first | A `StatefulSet` image bump and hope |
| Generated credentials in a `-app` Secret | A password in Git, or a manual `kubectl create secret` |
| A PodMonitor and an upstream Grafana dashboard | No visibility until something is already wrong |

Major-version upgrades are declarative too, but offline: ArgoCD reports
Degraded while one runs and the workload is down for its duration. Read
[the upstream guide](https://cloudnative-pg.io/documentation/current/postgres_upgrades/)
before merging one.

Databases claim `rook-ceph-block`, so Ceph's three copies already sit under
every database and a single-instance `Cluster` is a defensible default: the
data survives a node loss even when the process does not. Where a workload
cannot tolerate the restart, `instances: 3` is the change.

## Usage

### The contract

**Every workload that needs PostgreSQL gets a CloudNativePG `Cluster`. No
chart-bundled database, ever.** A bundled Postgres moves when the
application's chart decides — a major version jump inside someone else's patch
release; a `Cluster` moves when its `imageName` changes, a Renovate PR with a
human in front of it. Most bundled subcharts are also Bitnami's, whose
registry terms keep changing. The rule for the
[workloads repository](../development/add-workload.md):

1. Disable the subchart (`postgresql.enabled: false` or
   `internalDatabase.enabled: false`) and point the chart at the `Cluster`.
2. Never copy the password. CloudNativePG writes `username`, `password`,
   `host`, `port`, `dbname` and a ready-made `uri` to `<cluster-name>-app`;
   charts take those through an `existingSecret` block, and applications
   without one read `uri` as an environment variable. Nothing goes into Git
   or OpenBao — nothing outside the cluster ever needs this secret.
3. Put the `Cluster` in the workload's namespace, not `cnpg-system`, so the
   namespace's `CiliumNetworkPolicy` governs port 5432 and deleting the
   namespace takes the database with it.

## Health check

```bash
kubectl get clusters.postgresql.cnpg.io -A
kubectl cnpg status -n <namespace> <cluster-name>
```

`Cluster in healthy state` is the phrase; ArgoCD's built-in health check reads
the same field, which lets a sync wave wait for a database before the
application in front of it. The plugin prints the primary, replication lag,
WAL position and last failover.

## Pitfalls

!!! warning "Backups are Velero's job, for now"
    CloudNativePG can stream WAL to S3 and the object store exists, but nothing is configured to do it yet. A workload database is protected exactly as far as its PVC is: a crash-consistent CSI snapshot, recovered to a snapshot boundary rather than a point in time. See [Backups & Recovery](../operations/backups.md).
