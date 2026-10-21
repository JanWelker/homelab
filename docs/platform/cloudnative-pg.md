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
| Depends on | [Rook-Ceph](rook-ceph.md) for the database volumes and the backup buckets; [cert-manager](cert-manager.md) for the backup plugin's gRPC certificates |
| If it is down | Running databases keep running; failover, upgrades and new clusters stop |
| Health check | `kubectl get clusters.postgresql.cnpg.io -A` &rarr; `Cluster in healthy state` |
| Backups | Continuous WAL and a nightly base backup per database, to a bucket of its own |
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
cannot tolerate the restart, `instances: 3` is the change, as
[Authentik](authentik.md#database) shows.

### Backups

The [Barman Cloud plugin](https://cloudnative-pg.io/plugin-barman-cloud/)
runs beside the operator and injects a sidecar into every instance that names
an `ObjectStore`. The sidecar archives each WAL segment as PostgreSQL closes
it and takes the base backups a `ScheduledBackup` asks for, so a database
restores to any second inside the retention window, not just to last night.

| Choice | Why |
| --- | --- |
| The plugin, not `spec.backup.barmanObjectStore` | The in-tree field is deprecated upstream and is removed in a later release |
| One bucket per database, claimed in its own namespace | The bucket's keys exist only there, so a compromised workload can read or delete its own backups and nobody else's |
| `https://s3.infra.k8s.wlkr.ch`, not the plain RGW Service | Backups hold every row of the database; the Gateway's TLS is the same path Velero uses |
| Base backup nightly, after Velero | The two never contend for the object store; WAL archiving covers the hours between |
| Retention equal to Velero's TTL | One window to reason about: what Velero can no longer restore, the WAL archive cannot either |
| Preview databases have none | They are rebuilt from the pull request and deleted with it |

`ObjectStore` is a namespaced resource with no cluster-scoped counterpart, so
the per-database part is a short, identical stanza (below); everything else
is shared: the plugin, and the `cnpg-backups` policy in
`payload/platform/security/network-policies/cluster-wide.yaml` that lets any
CloudNativePG pod reach the object store.

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
4. Set `enablePDB: false`. The operator's `<cluster>-primary`
   PodDisruptionBudget refuses the primary's eviction outright, so a node
   drain hangs on it until [Kured](kured.md) gives up and the node is never
   rebooted. Without the budget a single instance is down for the move to
   another node and a three-instance cluster fails over within a minute.
5. Back it up with the [stanza below](#backing-up-a-database). Velero's
   nightly PVC snapshot is crash-consistent and restores only to the night it
   was taken; the WAL archive restores to any second.

### Backing up a database

Next to the `Cluster`, in the same namespace, with `<db>` the cluster's
name. The bucket name is cluster-wide, hence the namespace in it; the sync
wave puts the store ahead of the `Cluster` that names it.

```yaml
---
apiVersion: objectbucket.io/v1alpha1
kind: ObjectBucketClaim
metadata:
  name: <db>-backup
  namespace: <namespace>
  annotations:
    argocd.argoproj.io/sync-options: Prune=false
    argocd.argoproj.io/sync-wave: "-2"
spec:
  bucketName: cnpg-<namespace>
  storageClassName: ceph-bucket
---
apiVersion: barmancloud.cnpg.io/v1
kind: ObjectStore
metadata:
  name: <db>
  namespace: <namespace>
  annotations:
    argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true
    argocd.argoproj.io/sync-wave: "-2"
spec:
  retentionPolicy: 14d
  configuration:
    destinationPath: s3://cnpg-<namespace>/
    endpointURL: https://s3.infra.k8s.wlkr.ch
    s3Credentials:
      accessKeyId: {name: <db>-backup, key: AWS_ACCESS_KEY_ID}
      secretAccessKey: {name: <db>-backup, key: AWS_SECRET_ACCESS_KEY}
    wal:
      compression: gzip
    data:
      compression: gzip
---
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: <db>
  namespace: <namespace>
spec:
  schedule: "0 0 3 * * *"
  immediate: true
  backupOwnerReference: self
  cluster:
    name: <db>
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
```

And in the `Cluster`:

```yaml
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      enabled: true
      isWALArchiver: true
      parameters:
        barmanObjectName: <db>
```

`enabled: true` is the webhook's default; written out, ArgoCD's diff matches
the live object, since `plugins` is compared as a whole list. Adding the
plugin restarts every instance to inject the sidecar: a
single-instance database is down for that restart, a replicated one switches
over. `immediate: true` takes the first base backup at once, so the WAL
archive is restorable from the first day.

## Health check

```bash
kubectl get clusters.postgresql.cnpg.io -A
kubectl cnpg status -n <namespace> <cluster-name>
```

`Cluster in healthy state` is the phrase; ArgoCD's built-in health check reads
the same field, which lets a sync wave wait for a database before the
application in front of it. The plugin prints the primary, replication lag,
WAL position and last failover.

```bash
kubectl get backups.postgresql.cnpg.io -A           # Phase completed, one per night
kubectl get clusters.postgresql.cnpg.io -A \
  -o custom-columns='NS:.metadata.namespace,NAME:.metadata.name,ARCHIVING:.status.conditions[?(@.type=="ContinuousArchiving")].status'
kubectl get objectstores.barmancloud.cnpg.io -A -o yaml | grep -A3 serverRecoveryWindow
```

`ContinuousArchiving` `True` means the last WAL segment reached the bucket;
`False` means WAL is piling up on the instance's volume, which fills it. The
recovery window lives on the `ObjectStore`, per server name: the `Cluster`'s
own backup fields stay empty with a plugin.

## Pitfalls

- **A stuck archive fills the volume.** PostgreSQL keeps every WAL segment the
  sidecar has not archived; a broken bucket or policy shows as
  `ContinuousArchiving` `False` long before the PVC is full. Fix the cause,
  never delete `pg_wal`.
- **A restore is a new `Cluster`.** Recovery bootstraps a fresh cluster from
  the bucket; it never rewinds a running one. Give the new cluster its own
  `ObjectStore` destination, or its archive would mix with the one it was
  restored from.

## Recovery

### Point-in-time restore

1. Pick the target time (UTC) and check it lies inside the window:

   ```bash
   kubectl -n <namespace> get objectstore <db> -o jsonpath='{.status.serverRecoveryWindow}{"\n"}'
   ```

2. In the workloads repository, rename the `Cluster` to `<db>-restore` and
   replace `bootstrap.initdb` with a recovery from the old store, keeping the
   old name as `serverName`:

   ```yaml
     bootstrap:
       recovery:
         source: origin
         recoveryTarget:
           targetTime: "2026-10-21 12:00:00+00"
     externalClusters:
       - name: origin
         plugin:
           name: barman-cloud.cloudnative-pg.io
           parameters:
             barmanObjectName: <db>
             serverName: <db>
   ```

   Its own `plugins` entry keeps the same `barmanObjectName` with
   `serverName: <db>-restore`, so the new archive lands beside the old one.

3. Merge, wait for `Cluster in healthy state`, then point the application at
   `<db>-restore-app` and remove the old `Cluster` in a second PR.
