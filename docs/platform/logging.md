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

## At a glance

| | |
| --- | --- |
| Namespace | `logging` |
| Stage | `08-services` for `logging` (bucket, dashboards), `09-backends` for Loki, `10-agents` for Alloy — the collector last, so it has somewhere to ship |
| Depends on | [Rook-Ceph](rook-ceph.md) object storage for chunks, [Monitoring](monitoring.md) for the Grafana that queries it |
| If it is down | Logs stop being collected and are not backfilled. The [audit log](../architecture/audit-logging.md) loses its durable copy |
| Health check | `kubectl -n logging get pods`, then a `{job="kubernetes-audit"}` query in Grafana |
| Metrics | Loki and Alloy, each through its chart's `ServiceMonitor`; Alloy's dashboards are listed under [Monitoring](monitoring.md#dashboards) |

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

The audit log is a different case again. It exists only on the three
control-plane nodes and it is JSON rather than text, so it gets its own pipeline.
It lives on the root filesystem and survives a reboot there — collecting it is
still what makes it queryable next to everything else, and what keeps a copy
when the node itself is the thing that failed. See
[Audit logging](../architecture/audit-logging.md) for what is recorded
and at which level. Alloy runs on all six nodes and `local.file_match` simply
finds nothing on the workers, which is a cheaper way to say "control plane only"
than any scheduling constraint. It does need a toleration for the
control-plane `NoSchedule` taint, though: without one Alloy runs on the workers
alone, and neither the audit log nor the control-plane kubelets' journals reach
Loki.

All three sources Alloy reads — `/var/log/pods`, `/var/log/journal` and
`/var/log/kubernetes/audit` — are directories on the 50GB root filesystem, so
one `varlog: true` mount in the chart covers all three.

Container logs pass through `stage.cri {}`. containerd writes
`<timestamp> <stream> <flags> <message>`; without that stage the timestamp and
stream end up inside the log line and Loki stamps everything at ingest time —
which quietly destroys the one property you actually needed, namely being able
to line logs up against the incident. Audit events get the same treatment from
`stage.timestamp`, which stamps each one with its `requestReceivedTimestamp`
rather than the moment Alloy read the file.

The journal's `job="systemd-journal"` label is set by a relabel rule, not the
source's `labels` argument. `loki.source.journal` overwrites `job` with its
component ID after applying `labels`, and only the relabel rules run after that.

### Running Alloy

Alloy runs as root (`runAsUser: 0`). The log files are root-owned with
restrictive modes, and a non-root Alloy does not fail — it silently collects
nothing. Everything else is locked down around that: a read-only root
filesystem, no capabilities, no privilege escalation, and the `RuntimeDefault`
seccomp profile, which is worth having precisely because the process is root.

The read-only root has one side effect. Alloy writes its data directory to
`storagePath`, which the chart defaults to `/tmp/alloy`, and every pod would die
at startup with `mkdir /tmp/alloy: read-only file system`. An `emptyDir` mounted
at `/tmp` restores what the chart assumes. It holds file-tailing positions and
does not survive a restart — nor would the container layer it replaces.

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

The chart already runs Loki as UID 10001 with a read-only root and no
capabilities; `loki.podSecurityContext` adds the `RuntimeDefault` seccomp
profile at pod level, which covers the rules sidecar too. It has to be that key:
`singleBinary.podSecurityContext` does not exist, so setting it there is
silently inert.

The sidecars have limits of their own: Loki's rules sidecar (measured
peak 72Mi, limit 192Mi) and Alloy's config reloader (peak 12Mi, given the
platform's 128Mi floor rather than a measured figure).

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

## Dashboards

`grafana-dashboards.yaml` carries three dashboards from the Alloy mixin
(`operations/alloy-mixin/rendered/dashboards/` in grafana/alloy), unmodified, at
the Alloy version the chart deploys. The chart renders none, hence the vendored
copy — and Renovate does not see it, so re-copy them when Alloy moves a minor
version.

The mixin's other dashboards are left out on purpose. Clustering, OpenTelemetry
and Prometheus remote-write are features this Alloy does not use, and "Logs
Overview" reads Alloy's own logs by a `job` label this pipeline never sets. The
`cluster` variable on the remaining three has nothing to list, since no scrape
sets a `cluster` label; its empty value matches series without one.

## Directory Structure

```text
loki/
└── application.yaml          # ArgoCD Application (Helm: grafana/loki)

alloy/
└── application.yaml          # ArgoCD Application (Helm: grafana/alloy)

logging/
├── application.yaml          # Directory Application (wraps the rest)
├── bucket.yaml               # ObjectBucketClaim for Loki's chunks
├── grafana-dashboards.yaml   # Alloy mixin dashboards, vendored
└── grafana-datasource.yaml   # Loki datasource, applied into monitoring/
```
