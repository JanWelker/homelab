---
description: "Loki and Grafana Alloy: what is collected, where it is stored, and why Promtail is not used."
---

# Logging

[Loki](https://grafana.com/oss/loki/) stores logs;
[Grafana Alloy](https://grafana.com/docs/alloy/latest/) collects them. Both are
queried from the existing Grafana at
[monitoring.infra.k8s.wlkr.ch](https://monitoring.infra.k8s.wlkr.ch).

Metrics answer *what* is happening; logs answer *why*. Without them, diagnosing
a crashed pod means racing the scheduler to `kubectl logs --previous` before the
evidence is garbage-collected — a game you lose roughly every time it matters.
Node-level problems are worse: visible only over SSH, on the node that is
currently misbehaving.

!!! note "Not Promtail"
    Promtail is the collector most Loki documentation still shows. It was
    deprecated in early 2025 and reached **end of life in March 2026**. Alloy is
    its supported replacement and the one to reach for now.

## What is collected

| Source | Where it comes from | Labels |
| --- | --- | --- |
| Container logs | `/var/log/pods/<ns>_<pod>_<uid>/<container>/*.log` | `namespace`, `pod`, `container`, `node`, `app` |
| Node journal | `/var/log/journal` | `unit`, `node`, `job="systemd-journal"` |
| API server audit log | `/var/log/kubernetes/audit/audit.log` | `verb`, `audit_level`, `node`, `job="kubernetes-audit"` |

The journal matters more here than it would elsewhere. On Flatcar, `kubelet`,
`containerd`, `systemd-sysupdate` and `update-engine` log to journald and
nowhere else. Without collection, the logs explaining a failed boot or a stuck
sysext are reachable only over SSH — which is exactly the moment SSH is least
convenient, and occasionally the moment it is not available at all.

Each Alloy pod discovers **only pods on its own node**, via a
`spec.nodeName` field selector. Without it every one of the six agents would
watch every pod in the cluster and discard all but its own — six times the API
server load for identical output. Log collectors are famously good at costing
more than the thing they observe; this is one of the cheap ways to avoid that.

The audit log is a different case again, and the only source here where Loki is
not a convenience. It exists only on the three control-plane nodes, it is JSON
rather than text, and it lives on the `varlog` partition — which, like every
filesystem these nodes mount, is reformatted on every boot. So the copy in Loki
is the one that outlives a reboot. See
[Audit logging](../architecture/security.md#audit-logging) for what is recorded
and at which level. Alloy runs on all six nodes and `local.file_match` simply
finds nothing on the workers, which is a cheaper way to say "control plane only"
than any scheduling constraint.

!!! note "`/var/log` is a partition, not the root filesystem"
    All three sources Alloy reads — `/var/log/pods`, `/var/log/journal` and
    `/var/log/kubernetes/audit` — sit on a dedicated 10GB XFS partition rather
    than on the tmpfs root. That is what stops log volume from being charged to
    the same RAM etcd runs in, and it is why the audit log can keep the ten
    rotations CIS asks for. The chart mount is unchanged: one `varlog: true`
    still covers all three.

Container logs pass through `stage.cri {}`. containerd writes
`<timestamp> <stream> <flags> <message>`; without that stage the timestamp and
stream end up inside the log line and Loki stamps everything at ingest time —
which quietly destroys the one property you actually needed, namely being able
to line logs up against the incident.

## Storage

Loki runs as a single binary and keeps chunks in the
[Ceph object store](rook-ceph.md#object-storage) rather than on a PVC.

| Property | Value |
| --- | --- |
| Deployment mode | `SingleBinary`, 1 replica |
| Chunks + ruler | S3 bucket `loki`, via the `loki-bucket` ObjectBucketClaim |
| Endpoint | `rook-ceph-rgw-object-store.rook-ceph.svc`, path-style, plain HTTP in-cluster |
| Local PVC | 10Gi on `rook-ceph-block`, for the WAL and the index being built |
| Retention | 30 days, compactor enabled |

The bucket uses a fixed `bucketName` rather than `generateBucketName`. A
generated name carries a random suffix, which would have to be read back out of
the ConfigMap and injected at runtime; a fixed name keeps the storage config
static and, more usefully, keeps it the same after a rebuild.

Credentials are never written to Git. Rook puts them in a Secret when it
provisions the claim, Loki reads them as environment variables, and the config
refers to `${AWS_ACCESS_KEY_ID}` — which is why `-config.expand-env=true` is
set.

!!! warning "Those settings belong on `singleBinary`, not `global`"
    The chart's `global.extraArgs` and `global.extraEnvFrom` look like the right place — the name says global, the documentation implies global — but the single-binary StatefulSet template reads only `singleBinary.extraArgs` and `singleBinary.extraEnvFrom`. Setting them globally renders a config full of unexpanded `${...}` and a Loki that cannot authenticate to RGW, with an error message that points nowhere near the actual cause.

`chunksCache` and `resultsCache` are off. They are memcached deployments and
would add four pods in front of a Loki this size — caching infrastructure larger
than the thing it caches is a decision best left to people with more logs.

## Querying

The Loki datasource is registered with Grafana automatically. In Grafana,
choose **Loki** as the datasource and query by label:

```logql
{namespace="rook-ceph"} |= "error"
{unit="kubelet.service", node="odin"}
{namespace="openbao"} |= "sealed"
{job="kubernetes-audit"} | json | objectRef_resource="secrets"
```

Audit events keep only `verb` and `audit_level` as labels. Both are small closed
sets, which is what makes a label cheap; user and resource are far more useful
to query by and far too numerous to label, so they stay in the line where
`| json` can reach them.

!!! note "Where the datasource lives"
    `grafana-datasource.yaml` declares its namespace as `monitoring`, not
    `logging`. The kube-prometheus-stack Grafana sidecar only watches its own
    release namespace for `grafana_datasource` ConfigMaps, so one placed next to
    Loki would never be picked up. It is defined with the component it
    describes and applied where Grafana can see it.

## Directory Structure

```text
logging/
├── application.yaml          # Directory Application (wraps the rest)
├── loki.yaml                 # ArgoCD Application (Helm: grafana/loki)
├── alloy.yaml                # ArgoCD Application (Helm: grafana/alloy)
├── bucket.yaml               # ObjectBucketClaim for Loki's chunks
└── grafana-datasource.yaml   # Loki datasource, applied into monitoring/
```
