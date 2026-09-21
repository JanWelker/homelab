---
description: "Loki and Grafana Alloy: what is collected, where it is stored, and why Promtail is not used."
---

# Logging

[Loki](https://grafana.com/oss/loki/) stores logs;
[Grafana Alloy](https://grafana.com/docs/alloy/latest/) collects them. Both are
queried from the existing Grafana at
[monitoring.infra.k8s.wlkr.ch](https://monitoring.infra.k8s.wlkr.ch). Without
them a crashed pod's logs are gone with the container, and on Flatcar the
`kubelet`, `containerd`, `systemd-sysupdate` and `update-engine` logs live in
journald and are reachable only over SSH to the node that is misbehaving.

## At a glance

| | |
| --- | --- |
| Namespace | `logging` |
| Stage | `08-services` for `logging` (bucket, dashboards), `09-backends` for Loki, `10-agents` for Alloy — the collector last, so it has somewhere to ship |
| Depends on | [Rook-Ceph](rook-ceph.md) object storage for chunks, [Monitoring](monitoring.md) for the Grafana that queries it |
| If it is down | Logs stop being collected and are not backfilled. The [audit log](../architecture/audit-logging.md) loses its durable copy, and the [log alerts](#alerting) stop |
| Health check | `kubectl -n logging get pods`, then a `{job="kubernetes-audit"}` query in Grafana |
| Metrics | Loki and Alloy, each through its chart's `ServiceMonitor`; Alloy's dashboards are listed under [Monitoring](monitoring.md#dashboards) |
| Files | `payload/platform/logging/`, `payload/platform/loki/`, `payload/platform/alloy/` |

!!! note "Not Promtail, and not the `grafana/loki` chart"
    Promtail reached end of life in March 2026; Alloy is its supported replacement. Loki comes from `grafana-community/loki`: the OSS chart moved to [grafana-community/helm-charts](https://github.com/grafana-community/helm-charts) in March 2026, and what stayed in `grafana/loki` is maintained for Grafana Enterprise Logs customers, pins a `k8s-sidecar` several minor releases behind and closes open-source pull requests with a pointer to the community repository.

## Configuration

### Sources

| Source | Path | Labels |
| --- | --- | --- |
| Container logs | `/var/log/pods/<ns>_<pod>_<uid>/<container>/*.log` | `namespace`, `pod`, `container`, `node`, `app` |
| Node journal | `/var/log/journal` | `unit`, `node`, `job="systemd-journal"` |
| API server audit log | `/var/log/kubernetes/audit/audit.log` | `verb`, `audit_level`, `node`, `job="kubernetes-audit"` |

All three are on the root filesystem, so one `varlog: true` mount covers them.
The audit log exists only on the control-plane nodes; Alloy runs everywhere and
`local.file_match` finds nothing on the workers. Audit events keep only `verb`
and `audit_level` as labels — small closed sets — while user and resource stay
in the line where `| json` can reach them. See
[Audit logging](../architecture/audit-logging.md) for what is recorded.

### Alloy pipeline

| Stage | Why |
| --- | --- |
| `discovery.kubernetes` with a `spec.nodeName` field selector | Each agent discovers only its own node's pods; without it six agents watch every pod and discard all but their own |
| `stage.cri {}` on container logs | containerd writes `<timestamp> <stream> <flags> <message>`; without it the timestamp lands inside the line and Loki stamps everything at ingest time |
| `discovery.relabel "journal"` sets `job` | `loki.source.journal` overwrites `job` with its component ID after applying `labels`; only relabel rules run after that |
| `stage.json`, `stage.labels`, `stage.timestamp` on audit events | Stamps each event with its `requestReceivedTimestamp` rather than when Alloy read the file |

| Setting in `alloy/application.yaml` | Why |
| --- | --- |
| Control-plane toleration | Without it Alloy runs on the workers alone and neither the audit log nor the control-plane kubelets' journals reach Loki |
| `runAsUser: 0` | The log files are root-owned; a non-root Alloy does not fail, it silently collects nothing. Read-only root, no capabilities and `RuntimeDefault` seccomp lock the rest down |
| `emptyDir` at `/tmp` | The chart's `storagePath` defaults to `/tmp/alloy`, unwritable on a read-only root; it holds tailing positions, which did not survive a restart before either |

### Storage

Loki runs as a single binary (`deploymentMode: Monolithic`) and keeps chunks
in the [Ceph object store](rook-ceph.md#object-storage); a local PVC on
`rook-ceph-block` holds the WAL and the index being built.

| Setting in `loki/application.yaml` | Why |
| --- | --- |
| Fixed `bucketName` on the `ObjectBucketClaim` | A generated name carries a random suffix that would have to be read back at runtime; a fixed one keeps the config static and identical after a rebuild |
| `singleBinary.extraArgs` `-config.expand-env=true` and `singleBinary.extraEnvFrom` | Rook writes the bucket credentials to a Secret, Loki reads them as `${AWS_ACCESS_KEY_ID}` — nothing in Git |
| `chunksCache` and `resultsCache` off | Four memcached pods in front of a Loki this size |
| `loki.podSecurityContext` | Adds `RuntimeDefault` seccomp at pod level, covering the rules sidecar too |
| Retention with the compactor enabled | Old chunks are actually deleted |
| `rulerConfig` | The ruler evaluates LogQL alerts and posts them to Alertmanager; without it the audit log and the journal are query-only. Rules come from local files the chart's sidecar copies out of `loki_rule` ConfigMaps, so `storage.type` is `local` rather than the bucket the chart would pick; `rule_path` is the ruler's scratch directory and must not be the rules directory |

### Alerting

Alerts over logs live in `logging/loki-rules.yaml`, a ConfigMap labelled
`loki_rule` in the same format as a `PrometheusRule` group with LogQL
expressions. The `k8s-sidecar-target-directory: fake` annotation puts the
file under the tenant directory the ruler reads (`fake` is the tenant when
`auth_enabled` is off). Prometheus-side rules stay with their components;
these are the events only a log carries.

| Alert | Fires on | Why it is a log alert |
| --- | --- | --- |
| `KubernetesAuditExecIntoPod` | A successful `exec` or `attach` | Only the [audit log](../architecture/audit-logging.md) records who ran what in which pod |
| `KubernetesAuditSecretReadByUser` | A Secret read by a user, not a `system:` identity | Every controller reads through a ServiceAccount; a user is a person with a kubeconfig |
| `KubernetesAuditRbacBindingChanged` | A ClusterRole or ClusterRoleBinding written by anything but the ArgoCD controller or a `kube-system` controller | Argo CD is the only intended writer; namespaced RoleBindings are left out because CloudNativePG reconciles one per database continuously |
| `KubernetesAuditForbiddenBurst` | More than ten 403s from one identity in ten minutes | What RBAC probing from a compromised pod looks like |
| `KubernetesAuditAnonymousRequest` | A successful anonymous request | The audit policy drops the health endpoints, the only legitimate anonymous paths |
| `NodeSshLogin` | An accepted SSH login on a node | Nothing routine logs in after provisioning |
| `NodeSshAuthFailures` | More than five failed SSH attempts on a node in ten minutes | Password authentication is off; repeats are a scan or a retried key |

To confirm the ruler loaded a rule:

```bash
kubectl -n logging port-forward svc/loki 3100:3100
curl -s localhost:3100/loki/api/v1/rules
```

### Dashboards

`grafana-dashboards.yaml` carries three dashboards from the Alloy mixin
(`operations/alloy-mixin/rendered/dashboards/`), unmodified; the chart renders
none and Renovate does not see the copy, so re-copy them when Alloy moves a
minor version. The mixin's other dashboards cover features this Alloy does not
use. The Loki datasource in `grafana-datasource.yaml` declares its namespace
as `monitoring`, the only namespace the Grafana sidecar watches for
`grafana_datasource` ConfigMaps.

## Usage

Choose **Loki** as the datasource in Grafana and query by label:

```logql
{namespace="rook-ceph"} |= "error"
{unit="kubelet.service", node="odin"}
{namespace="openbao"} |= "sealed"
{job="kubernetes-audit"} | json | objectRef_resource="secrets"
```

## Health check

```bash
kubectl -n logging get pods
```

Then run a `{job="kubernetes-audit"}` query in Grafana; an empty result with
healthy pods means the control-plane agents are not reading the file.

## Pitfalls

!!! warning "Loki settings belong on `singleBinary`, not `global`"
    `global.extraArgs` and `global.extraEnvFrom` look right, but the single-binary StatefulSet reads only `singleBinary.extraArgs` and `singleBinary.extraEnvFrom`. Set globally, the config is full of unexpanded `${...}` and Loki cannot authenticate to RGW, with an error pointing nowhere near the cause. Likewise `singleBinary.podSecurityContext` does not exist and is silently inert.
