---
description: "Continuous vulnerability, misconfiguration, secret, RBAC and CIS scanning, with every finding stored as a CRD, and the settings that keep the reports from silently going missing."
---

# Trivy Operator

Aqua Security's [Trivy Operator](https://aquasecurity.github.io/trivy-operator/latest/)
scans everything the cluster runs and writes every result back as a Kubernetes
object. Reading those objects is [Vulnerability Triage](../operations/vulnerabilities.md);
this page is the operator itself.

## At a glance

| | |
| --- | --- |
| Namespace | `trivy-system` |
| Stage | `11-policy`; nothing above it depends on a scan |
| Depends on | [Monitoring](monitoring.md) for the `ServiceMonitor` and the dashboard, [Rook-Ceph](rook-ceph.md) for the vulnerability database volume |
| If it is down | Reports expire after 24h and are not rebuilt; `TrivyContainerNotScanned` fires for every container |
| Health check | `kubectl -n trivy-system get pods,jobs`, then the reconciliation below |
| Dashboard | [Trivy Operator](https://monitoring.infra.k8s.wlkr.ch/d/trivy-operator-overview) |
| Files | `payload/platform/trivy-operator/`: the chart, `prometheusrule.yaml`, the `findings-history` CronJob and a dashboard written for this cluster. The namespace labels and the default-deny policy are in [Security Policies](security-policies.md) |

| Scanner | Produces | Answers |
| --- | --- | --- |
| Vulnerability | `VulnerabilityReport` | Which CVEs are in the images we run |
| Exposed secret | `ExposedSecretReport` | Which images have credentials baked in |
| Config audit | `ConfigAuditReport` | Which workloads are misconfigured |
| RBAC assessment | `RbacAssessmentReport` | Which roles grant too much |
| Infra assessment | `InfraAssessmentReport` | Whether the nodes pass CIS |
| Compliance | `ClusterComplianceReport` | CIS 1.23, NSA/CISA 1.0, PSS baseline and restricted |

The `eks-` and `rke2-` compliance specs are for other distributions and are
left out rather than reported as failing.

## Configuration

The values in `application.yaml` that are not defaults:

| Setting | Why |
| --- | --- |
| `scannerReportTTL: 24h`, `scanOnlyCurrentRevisions`, `scanJobTTL: 10m` | Reports live in etcd. A stale report is rebuilt, superseded ReplicaSets are not scanned, and finished jobs do not accumulate |
| `builtInTrivyServer: true` | `ClientServer` mode: one `trivy-server` holds the vulnerability database on the PVC. Standalone mode downloads it in every scan job, which meets registry rate limits, and a rate-limited scan reports zero findings rather than an error |
| `ignoreUnfixed: false`, every severity | A finding with no fix is usually the signal that the base image is wrong, and a finding the scanner could not grade is worth reading |
| `ignorePolicy.nextcloud`, `ignorePolicy.authentik` | Those images carry thousands of unfixed Debian findings and their reports exceed the etcd request limit. The Rego (v0 syntax, `object.get` because `FixedVersion` is absent when there is no fix) drops only findings that have no fix *and* are below HIGH |
| `additionalVulnerabilityReportFields` without `Description` and `Links` | A report is one API write, capped at 2 MiB; `Links` alone is over half the payload. An oversized report is rejected with nothing but an operator log line |
| `operator.scanJobTimeout` and `trivy.timeout`, both 20m | The first is the Job's `activeDeadlineSeconds` and kills the pod whatever Trivy is doing; the second is Trivy's own flag and only fires if it is the shorter. Raising one alone does nothing |
| `clusterSbomCacheEnabled: false` | With the cache, every workload after the first to use an image gets an `SbomReport` from the cached SBOM and no `VulnerabilityReport`. Upstream: [trivy-operator#1668](https://github.com/aquasecurity/trivy-operator/issues/1668) |
| `metricsVulnIdEnabled: false` | One series per CVE per container is a six-figure cardinality, and every label is already in the report |
| `compliance.reportType: summary` | The collector skips reports in `all` format, so `trivy_compliance_info` disappears and the dashboard's failing-controls table empties. The `status` label is title-cased: `Fail`, `Pass` |
| Control-plane tolerations on `nodeCollector` and `scanJobTolerations` | Without them the CIS infra assessment covers only the workers and reports a clean control plane it never looked at |
| Namespace `enforce: privileged`, `audit`/`warn: restricted` | node-collector hostPath-mounts `/var/lib/etcd`, `/var/lib/kubelet`, `/etc/kubernetes` and `/etc/cni/net.d`, which `baseline` forbids. Scan jobs themselves drop all capabilities and run read-only |
| `logDevMode: false` | Its `V(1)` lines are the only way to see decisions the operator makes silently, but it switches logging to console encoding. Turn it on to debug, then off |

### Alerting

Five rules in `prometheusrule.yaml`. Reports expire and are rebuilt every
24h, so a "new" finding is one absent from the reports two days ago rather
than from the previous scrape, and every rule waits longer than a rebuild.

| Alert | Fires when | Why this shape |
| --- | --- | --- |
| `TrivyContainerNotScanned` | A running container has had no `VulnerabilityReport` for 2h | A rejected report is never written, so it looks identical to a clean one; only reconciling against what runs finds it |
| `TrivyExposedSecretNew` | A secret finding, keyed by container, rule and path, was not reported two days ago | The known findings (snake-oil keys, a vendored SDK's public test key) never clear, so an absolute rule would fire forever |
| `TrivyRbacCriticalNew` | A Role or ClusterRole gains a critical finding it did not have two days ago | Every critical ClusterRole is a chart's operator role or Kubernetes' own, see [Vulnerability Triage](../operations/vulnerabilities.md#what-is-load-bearing); what matters is a new one |
| `TrivyConfigAuditCritical` | Any workload fails a critical config-audit check for 2h | Nothing does today, so the rule is absolute |
| `TrivyComplianceRegression` | A framework has more failing controls than a day ago, for 12h | Compliance is recomputed every six hours; two runs above yesterday is drift, not a rebuild |

The two "new" rules also require the operator to have been reporting two days
ago, so a fresh cluster does not page on every existing finding.

`TrivyContainerNotScanned` compares every running container against the
containers that have a `VulnerabilityReport` and names the difference:

```promql
count by (namespace, container) (
  kube_pod_container_info
  * on (namespace, pod) group_left ()
  (max by (namespace, pod) (kube_pod_status_phase{phase="Running"} == 1))
)
unless on (namespace, container)
count by (namespace, container) (
  label_replace(trivy_image_info, "container", "$1", "container_name", "(.+)")
)
```

- **Keyed on `(namespace, container)`, not the digest**: containerd's
  platform digest and the registry's tag digest differ permanently for
  mirrored images.
- **Restricted to `Running` pods**, or every completed Job counts as unscanned.
- **`for: 2h`**, several times a full sweep, so the 24h TTL churn does not page.

The dashboard's stat and by-severity panels take `max by (image_digest)`
before summing, and workload panels join ReplicaSets against
`kube_replicaset_spec_replicas > 0`, so a finding counts once per image and a
superseded revision drops out. The "Worst workloads" table stays per container
on purpose: it answers where a finding runs.

### History

Reports live 24h and Prometheus keeps ten days, so neither can answer whether a
count went up over a quarter. The `findings-history` CronJob in
`findings-history.yaml` lists every report kind once a day and prints one JSON
line per namespace, and per compliance framework, to stdout; Alloy ships it
like any container log and Loki keeps that one stream for a year
(`retention_stream` in `loki/application.yaml`). The dashboard's last row
reads it. Images are counted once per digest per namespace, the same rule the
dashboard applies.

```logql
{namespace="trivy-system", container="findings-history"} | json | kind="vulnerabilities" | namespace="argocd"
```

## Usage

The dashboard is a summary; the objects have the detail.

```bash
# What has been scanned, worst first
kubectl get vulnerabilityreports -A --sort-by='.report.summary.criticalCount'

# The findings themselves, for one workload
kubectl get vulnerabilityreports -n <ns> <name> -o json \
  | jq '.report.vulnerabilities[]
        | select(.severity=="CRITICAL")
        | {id: .vulnerabilityID, pkg: .resource,
           installed: .installedVersion, fixed: .fixedVersion}'

# Misconfiguration, RBAC and node checks
kubectl get configauditreports -A
kubectl get rbacassessmentreports,clusterrbacassessmentreports -A
kubectl get infraassessmentreports -A

# Which compliance controls are failing
kubectl get clustercompliancereports k8s-cis-1.23 -o json \
  | jq '.status.summary, (.status.summaryReport.controlCheck[]
        | select(.totalFail > 0))'
```

## Health check

Reconcile running images against scanned ones; treat a difference as a missing
scan until proven otherwise:

```bash
kubectl get pods -A -o json \
  | jq -r '.items[].spec.containers[].image' | sort -u | wc -l
kubectl get vulnerabilityreports -A -o json \
  | jq -r '.items[].report.artifact.repository' | sort -u | wc -l
```

!!! danger "Zero is not the same as clean"
    A report that reads zero may be a clean image or a scan that failed: a rate-limited pull, a timeout, an image the operator could not resolve. Check the object exists and carries a real digest before believing a zero, and look at `kubectl -n trivy-system get jobs` when one never appears. This is where the `vulnerability-triage` skill in `.claude/skills/` starts.

## Pitfalls

Four ways a workload ends up with no `VulnerabilityReport`, all silent from
the outside:

| Symptom | Cause | Where to look |
| --- | --- | --- |
| No report, no `SbomReport`, operator logs `ResourceExhausted ... larger than max` or `etcdserver: request is too large` | The report exceeds the API write ceiling | Operator log. Fix: the fields and ignore policies above |
| No report, Job events show `DeadlineExceeded` | `scanJobTimeout` elapsed on a large image | `kubectl -n trivy-system get events --field-selector reason=DeadlineExceeded`, before `scanJobTTL` deletes the Job |
| Some containers of a multi-container workload missing; job succeeded; log has `failed to analyze layer ... unexpected EOF` | A scan container died reading the image and the operator keeps only containers that exited 0. Hits workloads with several containers on one image (Rook, Cilium). Not the shared cache: concurrent scans with it pass | Scan job pod logs |
| `SbomReport` exists, no `VulnerabilityReport` | SBOM cache reuse path ran `trivy sbom` and produced nothing | Confirm `clusterSbomCacheEnabled` is off |

A completed scan job counts against `concurrentScanJobsLimit` until
`scanJobTTL` deletes it, so a backlog drains in batches of ten every ten
minutes; that is the expected shape, not a stall.
