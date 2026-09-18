---
description: "The PostgreSQL operator every workload database runs on, why a database is never a subchart here, and what the operator does that a StatefulSet does not."
---

# CloudNativePG

Half the applications worth running need PostgreSQL, and every Helm chart that
needs one ships its own. Accept those defaults and a cluster ends up with four
Postgres deployments of three different vintages, each upgraded by whoever
maintains the chart that buried it, each backed up — or not — in its own way.

CloudNativePG is the answer to that: one operator, in `cnpg-system`, and a
`Cluster` resource per database. It runs in `03-controllers` alongside the
other operators, because like them it brings its own CRDs and everything that
uses it comes much later.

## The contract

**Every workload that needs PostgreSQL gets a CloudNativePG `Cluster`. No
chart-bundled database, ever.**

That is a rule for the [workloads repository](../development/add-workload.md),
not a suggestion, and it is worth being explicit about what it costs and buys:

- **Disable the subchart.** Most charts default to a bundled database —
  `postgresql.enabled: false` or `internalDatabase.enabled: false` — and then
  accept an external one. Point that at the `Cluster` instead.
- **Never copy the password.** CloudNativePG generates the credentials and
  writes them to a `<cluster-name>-app` Secret with `username`, `password`,
  `host`, `port`, `dbname` and a ready-made `uri`. Charts take those keys
  through an `existingSecret` block; applications without one read `uri`
  straight out of the Secret as an environment variable. Nothing goes into
  Git, and nothing goes into OpenBao either — this is one of the few secrets
  the cluster is allowed to generate for itself, because nothing outside the
  cluster ever needs to know it.
- **The database lives in the workload's namespace**, not in `cnpg-system`.
  The operator is cluster-scoped; the data is not. That keeps the namespace's
  `CiliumNetworkPolicy` in charge of who can reach port 5432, and it means
  deleting a workload's namespace takes its database with it rather than
  leaving an orphan behind.

The reason for the rule is upgrades. A bundled Postgres moves when the
application's chart decides it moves, which in practice means a major version
jump arrives inside a patch release of something else. A `Cluster` moves when
its `imageName` changes, which is a Renovate PR with the major-version label
on it and a human in front of it.

The secondary reason is Bitnami. Most bundled Postgres subcharts are Bitnami's,
and Bitnami's registry terms have changed twice in recent memory — the
Nextcloud chart's own Redis subchart already points at `bitnamilegacy`. Charts
whose database is not used cannot break in that direction.

## What the operator actually does

A `Cluster` is not a StatefulSet with a nicer name. The parts that matter here:

| It handles | Instead of |
| --- | --- |
| Primary election and failover between instances | A single pod that takes the database down with it |
| `-rw` and `-ro` Services that follow the primary | Hardcoding a pod name and being wrong after a restart |
| Rolling minor-version upgrades, replicas first | A `StatefulSet` image bump and hope |
| Generated credentials in a `-app` Secret | A password in Git, or a manual `kubectl create secret` |
| A PodMonitor and an upstream Grafana dashboard | No visibility until something is already wrong |

Major-version upgrades are the exception: those are declarative too, but they
are an offline operation that ArgoCD reports as Degraded while it runs. Read
[the upstream guide](https://cloudnative-pg.io/documentation/current/postgres_upgrades/)
before merging one, and expect the workload to be down for its duration.

## Sizing and storage

Databases claim `rook-ceph-block` like everything else, so Ceph's replication —
three copies, one per host — is already underneath every database in the
cluster. That makes a single-instance `Cluster` a defensible homelab default:
the data survives a node loss even when the database process does not, and a
second instance costs another full copy of the data on top of Ceph's three.

Where a workload genuinely cannot tolerate the restart, `instances: 3` is the
change, and CloudNativePG handles the rest.

!!! warning "Backups are Velero's job, for now"
    CloudNativePG can stream WAL to S3 — and the cluster has an S3 endpoint in
    Ceph's object store — but nothing here is configured to do it yet. Until it
    is, a workload database is protected exactly as far as its PVC is: by the
    CSI snapshots Velero takes. That recovers a database to a snapshot boundary,
    not to a point in time, and it is a crash-consistent copy rather than a
    Postgres-consistent one. See [Backups &
    Recovery](../operations/backups.md).

## Verifying

```bash
kubectl get clusters.postgresql.cnpg.io -A
```

`Cluster in healthy state` is the phrase to look for; ArgoCD reads the same
field through its built-in health check, which is what makes a sync wave able
to wait for a database before starting the application in front of it.

For anything deeper, the operator ships a `kubectl` plugin:

```bash
kubectl cnpg status -n <namespace> <cluster-name>
```

It prints the primary, the replication lag, the WAL position and the last
failover, which is the set of questions worth asking when a database is slow
rather than down.
