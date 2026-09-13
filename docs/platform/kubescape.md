---
description: "Kubescape scans this cluster against CIS, NSA and MITRE on a schedule and exports the results to Grafana. What is enabled, what is deliberately not, and which failures are decisions."
---

# Kubescape

[Kubescape](https://kubescape.io/) is a CNCF project that evaluates the cluster
against published security frameworks — CIS, NSA-CISA, MITRE ATT&CK — and turns
the result into metrics. It runs nightly, stores findings in the cluster, and
exports them to the existing Prometheus for a
[Grafana dashboard](https://monitoring.infra.k8s.wlkr.ch/d/kubescape-vuln-overview).

The value is not the first scan. It is the second one, six months later, after a
chart bump quietly introduced a container running as root and nobody was looking
at that namespace. Posture is not a state you reach; it is a thing that decays,
and the only useful measurement of it is a repeated one.

## What it scans

`defaultFrameworks` is left empty, and empty means **all of them**. Naming
frameworks explicitly narrows scheduled scans, and a list pinned to a specific
benchmark revision goes stale in the worst possible way — it keeps scanning and
keeps passing, just not against the benchmark you believed you were measuring.

| | |
| --- | --- |
| Schedule | Daily, `1 2 * * *` |
| Frameworks | All, including CIS, NSA-CISA and MITRE ATT&CK |
| Node-level checks | Yes (`nodeScan`) — the kube-bench-shaped half of CIS |
| Results | Aggregated API: `spdx.softwarecomposition.kubescape.io` |
| Metrics | `kubescape_controls_*`, scraped by two ServiceMonitors |
| Sent off-cluster | **Nothing** |

That last row is a configuration choice, not a mode. Kubescape talks to the ARMO
SaaS backend when `server`, `account` and `accessKey` are set; leaving them unset
is what keeps findings in the cluster. There is no "offline" switch to forget to
flip, which is the right way round for a default.

## What is deliberately off

Kubescape ships as a suite and defaults to most of it enabled. Roughly half is
turned off here, and — in the spirit of the rest of this documentation — each one
is a decision with a reason rather than a default nobody examined.

### Image vulnerability scanning

`vulnerabilityScan`, `relevancy` and `nodeSbomGeneration` are off because
**kubevuln cannot schedule on these nodes**. It requests 5Gi of
`ephemeral-storage` and limits at 10Gi:

| Node | Allocatable ephemeral-storage |
| --- | --- |
| Control plane (`odin`, `thor`, `loki`) | 3.6 GB |
| Workers (`freya`, `heimdall`, `valkyrie`) | 7.4 GB |

The request alone makes the pod unschedulable on three of six nodes. The deeper
problem is what that storage *is*: the root filesystem here is
[tmpfs](../architecture/security.md#where-the-log-actually-lives), so ephemeral
storage is RAM. A container image unpacked for scanning is charged to the same
memory etcd is running in, on a node with 7.7 GB of it.

Turning it on is a real options trade, not a flag flip — it wants tuned
`kubevuln.resources`, a `nodeSelector` pinning it to the workers, and a lower
`maxImageSize`. Worth doing the day `/var/lib` sits on a disk.

### The runtime stack

`runtimeObservability` and its relatives (`networkPolicyService`,
`networkEventsStreaming`, `nodeProfileService`, `httpDetection`,
`seccompProfileService`) deploy an eBPF node-agent DaemonSet on all six nodes.
This is genuinely the most interesting half of Kubescape — it learns what a
workload normally does and flags when it stops doing that — and it is also the
most expensive, on nodes whose entire root filesystem is a 3.8 GB tmpfs.

Off until posture scanning has earned its keep. Enable one capability at a time
and watch node memory, not all of them at once.

### The admission controller

`admissionController` installs a validating webhook in front of the API server.
A posture tool that can refuse writes is a posture tool that can take the cluster
down when it is itself unhealthy, and a failing webhook fails in the least
convenient way available: `kubectl apply` starts returning errors about a
component most people have forgotten is in the path.

Nothing here is worth that trade while the findings are still being *read* rather
than enforced. Enforcement is a thing to earn.

!!! note "`autoUpgrading` and `manageWorkloads` are off for GitOps reasons"
    `autoUpgrading` upgrades Kubescape's own Helm release from inside the
    cluster — behind ArgoCD's back, and straight into a permanent OutOfSync.
    `manageWorkloads` lets the operator mutate the workloads it has findings
    about, and grants `patch` on nodes cluster-wide to do it.

## The dashboard

The dashboard JSON is upstream's, from
[kubescape/prometheus-exporter](https://github.com/kubescape/prometheus-exporter),
unmodified. It carries no datasource of its own, which is what makes it work
here: panels bind to Grafana's default datasource, and the Loki datasource next
door explicitly sets `isDefault: false`, leaving Prometheus holding that role.

| Panel | Metric | Populated |
| --- | --- | --- |
| Cluster Controls Vulnerabilities | `kubescape_controls_total_cluster_*` | Yes |
| Namespace Controls Vulnerabilities | `kubescape_controls_total_namespace_*` | Yes |
| Workload Controls Vulnerabilities | `kubescape_controls_total_workload_*` | Yes |
| Cluster Vulnerabilities | `kubescape_vulnerabilities_total_cluster_*` | No |
| Namespace Vulnerabilities | `kubescape_vulnerabilities_total_namespace_*` | No |

The two empty panels are the image-CVE half, disabled above. They are left in
place rather than edited out: a panel that is empty because a capability is off
is easier to reason about than a dashboard that silently diverged from upstream,
and it becomes correct again the moment the capability is enabled.

!!! note "Where the dashboard ConfigMap lives"
    `grafana-dashboard.yaml` declares its namespace as `monitoring`, not
    `kubescape` — the same reason as
    [the Loki datasource](logging.md#querying). The Grafana sidecar only watches
    its own release namespace for `grafana_dashboard` ConfigMaps.

## Reading results without Grafana

Findings are stored in an aggregated API server, so they are ordinary
`kubectl` objects:

```bash
# One row per framework, with the pass/fail counts
kubectl get configurationscansummaries -A

# Per-workload control failures, worst first
kubectl get workloadconfigurationscansummaries -A

# The full finding set for one workload
kubectl get workloadconfigurationscans -n <namespace> <name> -o yaml
```

## Failures that are decisions

A first scan on this cluster reports findings that are deliberate, and knowing
which in advance is the difference between a useful report and a report that
gets ignored:

| Finding | Why it is that way |
| --- | --- |
| `controller-manager` and `scheduler` bind to `0.0.0.0` | Required for the kube-prometheus-stack ServiceMonitors to reach them — see [monitoring](monitoring.md) |
| `--audit-log-maxbackup` is 2, not 10 | The audit log shares a 3.8 GB tmpfs with etcd; Loki holds the retention instead — see [Audit logging](../architecture/security.md#where-the-log-actually-lives) |
| Four namespaces enforce PSA `privileged` | Cilium, Rook OSDs, node-exporter and OpenBao each need it, individually — see [Security Policies](security-policies.md) |
| No default-deny egress anywhere | [Deliberate, and the next step](security-policies.md#scope) |

Kubescape has an exceptions mechanism for exactly this. It is not configured
here yet, on the grounds that a suppression list written before anyone has read
a real report is a list of guesses.

## Kubescape and kube-bench

[kube-bench](https://github.com/aquasecurity/kube-bench) is the reference CIS
implementation and is worth a one-off run for a second opinion; it maps findings
to numbered CIS controls (`1.2.16`, `1.3.7`) in a way Kubescape does not. Note
that its newest profile is `cis-2.0`, covering Kubernetes 1.34–1.35, so on
v1.37 auto-detection fails and the benchmark has to be pinned:

```bash
kube-bench run --benchmark cis-2.0 --targets master,controlplane,node,etcd,policies
```

Kubescape is what runs *continuously*, which is the part that matters. A
benchmark you run once is a screenshot.

## Namespace security level

`kubescape` is the only namespace in
[`pod-security.yaml`](security-policies.md) that enforces PSA `restricted`, and
it needed no exception to get there: every workload the chart renders already
runs non-root with `RuntimeDefault` seccomp, no privilege escalation and `ALL`
capabilities dropped.

That is worth stating because it does not survive enabling the runtime
capabilities — those add a privileged DaemonSet, and the namespace would have to
drop to `privileged` to accommodate it. The cost of that switch is visible here
rather than discovered afterwards.

## Directory Structure

```text
kubescape/
├── application.yaml         # ArgoCD Application (Helm: kubescape-operator)
└── grafana-dashboard.yaml   # Upstream dashboard, applied into monitoring/
```
