---
description: "Loki and Grafana Alloy: what is collected, where it is stored, and why Promtail is not used."
---

# Logging

[Loki](https://grafana.com/oss/loki/) stores logs;
[Grafana Alloy](https://grafana.com/docs/alloy/latest/) collects them. Both are
queried from the existing Grafana at
[monitoring.infra.k8s.wlkr.ch](https://monitoring.infra.k8s.wlkr.ch).

Metrics answer *what* is happening; logs answer *why*. Without them, diagnosing
a crashed pod means reaching it with `kubectl logs` before it is replaced, and
node-level problems are visible only over SSH.

!!! note "Not Promtail"
    Promtail is the collector most Loki documentation still shows. It was
    deprecated in early 2025 and reached **end of life in March 2026**. Alloy is
    its supported replacement and the one to reach for now.

## What is collected

| Source | Where it comes from | Labels |
| --- | --- | --- |
| Container logs | `/var/log/pods/<ns>_<pod>_<uid>/<container>/*.log` | `namespace`, `pod`, `container`, `node`, `app` |
| Node journal | `/var/log/journal` | `unit`, `node`, `job="systemd-journal"` |

The journal matters more here than it would elsewhere. On Flatcar, `kubelet`,
`containerd`, `systemd-sysupdate` and `update-engine` log to journald and
nowhere else. Without collection, the logs explaining a failed boot or a stuck
sysext are reachable only over SSH, which is exactly when SSH is least
convenient.

Each Alloy pod discovers **only pods on its own node**, via a
`spec.nodeName` field selector. Without it every one of the six agents would
watch every pod in the cluster and discard all but its own.

Container logs pass through `stage.cri {}`. containerd writes
`<timestamp> <stream> <flags> <message>`; without that stage the timestamp and
stream end up inside the log line and Loki stamps everything at ingest time.

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
static.

Credentials are never written to Git. Rook puts them in a Secret when it
provisions the claim, Loki reads them as environment variables, and the config
refers to `${AWS_ACCESS_KEY_ID}` — which is why `-config.expand-env=true` is
set.

!!! warning "Those settings belong on `singleBinary`, not `global`"
    The chart's `global.extraArgs` and `global.extraEnvFrom` look like the right
    place, but the single-binary StatefulSet template reads only
    `singleBinary.extraArgs` and `singleBinary.extraEnvFrom`. Setting them
    globally renders a config full of unexpanded `${...}` and a Loki that cannot
    authenticate to RGW.

`chunksCache` and `resultsCache` are off. They are memcached deployments and
would add four pods in front of a Loki this size.

## Querying

The Loki datasource is registered with Grafana automatically. In Grafana,
choose **Loki** as the datasource and query by label:

```logql
{namespace="rook-ceph"} |= "error"
{unit="kubelet.service", node="odin"}
{namespace="openbao"} |= "sealed"
```

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
