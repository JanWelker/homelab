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

## At a glance

| | |
| --- | --- |
| Namespace | `kubescape` |
| Sync wave | `2` |
| Depends on | [Monitoring](monitoring.md) for the Prometheus it exports to |
| If it is down | Nothing. This is the one component whose outage costs you only the next nightly scan |
| Health check | `kubectl get configurationscansummaries -A` |
| UI | A Grafana dashboard, not a UI of its own |

## What it scans

`defaultFrameworks` is left empty, and empty means **all of them**. Naming
frameworks explicitly narrows scheduled scans, and a list pinned to a specific
benchmark revision goes stale in the worst possible way — it keeps scanning and
keeps passing, just not against the benchmark you believed you were measuring.

| | |
| --- | --- |
| Posture schedule | Daily, `1 2 * * *` |
| Frameworks | All, including CIS, NSA-CISA and MITRE ATT&CK |
| Node-level checks | Yes (`nodeScan`) — the kube-bench-shaped half of CIS |
| Image CVEs | Yes (`vulnerabilityScan`), narrowed by `relevancy` |
| Runtime | eBPF node-agent DaemonSet on all six nodes |
| Results | Aggregated API: `spdx.softwarecomposition.kubescape.io` |
| Metrics | `kubescape_controls_*` and `kubescape_vulnerabilities_*`, three ServiceMonitors |
| Sent off-cluster | **Nothing** |

That last row is a configuration choice, not a mode. Kubescape talks to the ARMO
SaaS backend when `server`, `account` and `accessKey` are set; leaving them unset
is what keeps findings in the cluster. There is no "offline" switch to forget to
flip, which is the right way round for a default.

## Image vulnerability scanning

`kubevuln` requests 5Gi of `ephemeral-storage` and limits at 10Gi, which is why
this was not enabled when Kubescape first landed. The kubelet root directory was
part of the tmpfs root, so node allocatable `ephemeral-storage` was **3.6 GB** on
the control-plane nodes and **7.4 GB** on the workers — the request alone made
the pod unschedulable on half the cluster, and what it did allocate would have
been RAM shared with etcd.

`/var/lib/kubelet` is a directory on a
[125 GB root filesystem](../operations/nodes.md#rebuilding-or-repartitioning-a-node) now, so
the request is ordinary and the pod lands anywhere.

`relevancy` is the setting that makes the output worth reading. Without it a CVE
report lists every vulnerability in every layer of every image; with it, findings
are narrowed to packages actually loaded at runtime, which the node-agent
observes. That is the difference between a list of four hundred findings and a
list of the ones reachable in this cluster — and it is the other reason CVE
scanning and the runtime stack arrive together rather than separately.

## The runtime stack

`runtimeObservability` and its relatives deploy an eBPF node-agent DaemonSet on
all six nodes. It learns what a workload normally does — which syscalls, which
files, which network peers — and reports when it stops doing that.

| Capability | What it adds |
| --- | --- |
| `runtimeObservability` | The node-agent itself, and application profiles |
| `runtimeDetection` | Alerts on deviation from a learned profile |
| `networkPolicyService` | Generates `NetworkPolicy` from observed traffic |
| `networkEventsStreaming` | Feeds the above with live connection events |
| `nodeProfileService` | Per-node rather than per-workload profiles |
| `httpDetection` | Application-layer visibility on top of the network events |
| `seccompProfileService` | Generates seccomp profiles from observed syscalls |

The cost is memory: 180Mi requested and a 1400Mi limit on every node. That is
affordable now for the same reason CVE scanning is — the container logs and the
kubelet directory that used to occupy the tmpfs root moved to disk. `odin` was
sitting at 85% memory with 813 MB of it tmpfs before that change.

!!! tip "Watch the node-agent's own metrics first"
    `nodeAgent.serviceMonitor` is enabled alongside, so the agent's event rates
    and budget usage are in Prometheus from the moment it starts. If the runtime
    stack is going to be too expensive for these nodes, that is where it shows
    up — before the OOM killer makes the point less politely.

## What is still off

Four things, and none of them for capacity reasons — so none of them changed
when the nodes got disk.

**`malwareDetection`** scans file contents on the node for known signatures. A
different kind of expensive from the rest of the runtime stack, because it reads
rather than observes, and the one capability here without an obvious question it
answers about this cluster.

**`admissionController`** installs a validating webhook in front of the API
server. A posture tool that can refuse writes is a posture tool that can take the
cluster down when it is itself unhealthy, and a failing webhook fails in the
least convenient way available: `kubectl apply` starts returning errors about a
component most people have forgotten is in the path. Not worth the trade while
findings are still being *read* rather than enforced. Enforcement is a thing to
earn.

**`continuousScan`** re-evaluates posture on every relevant API change rather
than on the schedule. Useful on a cluster that changes constantly; this one
changes when a Renovate PR merges.

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

| Panel | Metric |
| --- | --- |
| Cluster Controls Vulnerabilities | `kubescape_controls_total_cluster_*` |
| Namespace Controls Vulnerabilities | `kubescape_controls_total_namespace_*` |
| Workload Controls Vulnerabilities | `kubescape_controls_total_workload_*` |
| Cluster Vulnerabilities | `kubescape_vulnerabilities_total_cluster_*` |
| Namespace Vulnerabilities | `kubescape_vulnerabilities_total_namespace_*` |

All five populate. The two `kubescape_vulnerabilities_*` panels were dark when
CVE scanning was disabled, and were deliberately left in the dashboard rather
than edited out — which is the reason they started working on their own rather
than needing the dashboard rewritten.

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
| Five namespaces enforce PSA `privileged` | Cilium, Rook OSDs, node-exporter, OpenBao and Kubescape's own node-agent each need it, individually — see [Security Policies](security-policies.md) |
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

`kubescape` enforces PSA `privileged`, and the node-agent is the entire reason.
Watching syscalls from userspace needs `hostPID`, `runAsUser: 0`, hostPath mounts
of `/`, `/boot`, `/sys/fs/bpf` and `/sys/kernel/debug`, and seven added
capabilities — `SYS_ADMIN`, `SYS_PTRACE`, `NET_ADMIN`, `SYSLOG`, `SYS_RESOURCE`,
`IPC_LOCK`, `NET_RAW`. No level below `privileged` admits that, and no amount of
tuning changes it.

This namespace briefly enforced `restricted`, before the runtime stack was
enabled — the five ordinary Deployments beside the node-agent all still satisfy
it on their own. PSA is per-namespace, so one DaemonSet sets the level for all
of them. `audit` and `warn` stay at `restricted` precisely so that the day one of
those five stops qualifying, it shows up in the
[audit log](../architecture/audit-logging.md) rather than becoming
invisible behind an `enforce` level that permits everything.

There is a certain symmetry in the cluster's security scanner being the thing
that needs the most privilege in it. It is also exactly the sort of finding
Kubescape will report about itself, which is the correct behaviour and worth not
suppressing.

## Directory Structure

```text
kubescape/
├── application.yaml         # ArgoCD Application (Helm: kubescape-operator)
└── grafana-dashboard.yaml   # Upstream dashboard, applied into monitoring/
```
