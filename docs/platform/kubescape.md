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
| Stage | `11-policy` |
| Depends on | [Monitoring](monitoring.md) for the Prometheus it exports to and its ServiceMonitor CRD; [Rook-Ceph](rook-ceph.md) for the results PVC |
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
| Results | Aggregated API: `spdx.softwarecomposition.kubescape.io`, backed by a `rook-ceph-block` PVC |
| Metrics | `kubescape_controls_*` and `kubescape_vulnerabilities_*` from the prometheus-exporter, per workload as well as per namespace; node-agent runtime metrics |
| Sent off-cluster | **Nothing** |

That last row is a configuration choice, not a mode. Kubescape talks to the ARMO
SaaS backend when `server`, `account` and `accessKey` are set; leaving them unset
is what keeps findings in the cluster. There is no "offline" switch to forget to
flip, which is the right way round for a default.

### The control plane is in scope, and it costs something

`excludeNamespaces` is set here rather than left at the chart default, which is:

```text
kubescape,kube-system,kube-public,kube-node-lease,kubeconfig,gmp-system,gmp-public
```

`kube-system` is dropped from that list. The default hides the entire control
plane — `kube-apiserver`, `etcd`, `kube-controller-manager`, `kube-scheduler`,
`coredns`, `metrics-server` and the CSI sidecars, fourteen distinct images that
reported no findings because nothing ever looked at them. That is the least
acceptable place in the cluster to have a blind spot, and the silence looks
exactly like a clean result.

`kubescape` itself stays excluded. Scanning the scanner is possible but adds
another six images to a component that is already the write bottleneck, and its
findings are the ones you can act on least directly.

!!! warning "This is not a free flip"
    The same value feeds the node-agent, so widening it widens **runtime
    profiling** too, not just image scanning — `kube-system` adds 77 containers
    on top of 304, about a quarter more `ContainerProfile` churn. The storage
    component serialises writes through a single SQLite writer, and at ~630
    profiles it already drops large writes on the floor: see
    [kubescape/storage#409](https://github.com/kubescape/storage/issues/409) and
    the section below. Widen the scope only once that is settled, or the new
    coverage arrives as more silently-missing manifests rather than as findings.

### The scanner image is pinned ahead of the chart

`kubescape.image.tag` overrides the chart's scanner image. Chart 1.40.4 ships
`kubescape` v4.0.13, and on v4.0.13 a scan of everything never runs: the
request lists every framework followed by every control, and the policy
download routes the whole batch by the kind of its first entry. Each control ID
is then fetched as a framework, and the nightly scan dies on the first one with
`framework 'C-0214' not found`. No `ConfigurationScanSummary` was ever written,
so every `kubescape_controls_*` series sat at zero — which reads like a clean
bill of health rather than a broken scanner.

v4.0.14 fixes the routing
([kubescape#3768](https://github.com/kubescape/kubescape/pull/3768)). Setting
`defaultFrameworks` would also avoid the bug, but only by pinning the list the
section above argues against. Renovate tracks the tag through its
`# renovate:` comment; remove the override once a chart release defaults to
v4.0.14 or later, or it will keep the scanner on whatever it last bumped to
regardless of what the chart was tested with.

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

### An image whose SBOM is too large is not scanned at all

`kubevuln.config.maxSBOMSize` is raised from the chart's 20Mi to 64Mi, because
Home Assistant does not fit in 20Mi and the way it does not fit is invisible.
The node-agent generates the SBOM, `kubevuln` weighs it, and an SBOM over the
cap is discarded rather than truncated:

```text
"incomplete or too large SBOM, skipping scan"
imageSlug: ghcr.io-home-assistant-home-assistant-2026.9.3-dda196
```

The `SBOMSyft` object is kept, which is the trap. It exists, it is named after
the image, and it holds nothing:

| Annotation | Value |
| --- | --- |
| `kubescape.io/resource-size` | 41651848 — **39.7Mi** |
| `kubescape.io/max-sbom-size` | 20971520 — **20Mi** |
| `kubescape.io/status` | `too-large` |
| `kubescape.io/status-reason` | `sbom-too-large` |
| `spec.syft.artifacts` | 0 |

So no `VulnerabilityManifest` is ever written, no summary either, and Home
Assistant reports **zero CVEs in both the loaded and whole-image columns** — the
one combination that reads as a clean bill of health rather than a missing scan.
A single Python image with several thousand packages was enough to cross the cap;
it is not an exotic case, and the next workload to cross it will fail the same
quiet way.

This is not `maxImageSize`, which is 5Gi and nowhere near binding — the Home
Assistant image is 0.65 GB compressed. Package count is what makes an SBOM
large, not bytes on disk.

The real ceiling on this setting lives in the storage component's `kindQueues`
rather than in `kubevuln`: `sbomsyfts` accepts objects up to 100000000 bytes and
`sbomsyftfiltereds` up to 50000000. 64Mi keeps the full SBOM inside the first
with room for a year of monthly Home Assistant releases. If a filtered SBOM ever
crosses the second, relevancy for that one image degrades and the whole-image
scan still lands.

!!! tip "The number to watch is node-agent memory, not kubevuln"
    Raising the cap means the node-agent now *keeps* a 40Mi SBOM it used to
    throw away, against a 1400Mi limit. The agent on the node running Home
    Assistant was already the hungriest of the six at ~860Mi before this
    change. `kubevuln` has far more room — a 5000Mi limit against ~600Mi in use.
    Nothing has restarted yet, but this is the figure that would move first.

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

### On the control plane too

The node-agent DaemonSet carries a toleration for
`node-role.kubernetes.io/control-plane`. The chart ships none, and without it the
agent ran on three of six nodes: the host scanner only ever collected from the
workers, and every CIS host control — PKI file permissions, kubelet config
ownership, CNI file ownership — reported a failure count as though that were the
whole cluster. The control-plane nodes hold `apiserver.key`, `sa.key` and the
etcd certificates; they were the unscanned half.

The toleration is set on `nodeAgent.tolerations`, not `customScheduling`. The
latter is global and would also let `kubescape`, `kubevuln`, the operator and
storage schedule onto the control plane, which is load those nodes do not need.
`values.yaml` does not document the per-component key, but the chart honours it:
in `templates/node-agent/_node-agent.tpl` it takes precedence over the global.

!!! tip "Watch the node-agent's own metrics first"
    `nodeAgent.serviceMonitor` and `nodeAgent.config.prometheusExporter` are
    enabled alongside — the second is what makes the agent listen on its
    metrics port at all — so the agent's event rates
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

## Metrics

There is deliberately no `kubescape.serviceMonitor`. Its endpoint,
`/v1/metrics`, is not a metrics page but a trigger: every scrape runs a full
posture scan (every 200 s at the chart's interval) and answers with the result.
Here those scans failed on the [v4.0.13 bug](#the-scanner-image-is-pinned-ahead-of-the-chart)
and the target sat at HTTP 500. The
`kubescape_controls_*` and `kubescape_vulnerabilities_*` series come from the
separate prometheus-exporter instead, which reports the stored results of the
scheduled scans.

`capabilities.prometheusExporter` and `nodeAgent.config.prometheusExporter` are
unrelated despite the name. The first deploys that exporter; the second starts
the node-agent's own `/metrics` listener on port 8080, replacing an OTLP push
that is not configured. Without it, the node-agent ServiceMonitor scrapes a port
nothing listens on.

## The dashboard

The dashboard is written for this cluster, not vendored. The upstream one from
[kubescape/prometheus-exporter](https://github.com/kubescape/prometheus-exporter)
answered "how many" and nothing after it, and on this cluster it did not manage
that either:

- **Every namespace panel showed one series, `kubescape`.** The exporter sets a
  `namespace` label, and Prometheus renames a target's clashing label to
  `exported_namespace` unless the scrape sets `honorLabels`. The chart's
  ServiceMonitor has no such option, so the queries group by
  `exported_namespace` instead.
- **It charted whole-image CVE counts only.** The exporter also publishes
  `kubescape_vulnerabilities_relevant_*`, narrowed by `relevancy` to packages
  the node-agent saw loaded, and those are the numbers worth acting on: 67
  critical against 261 when this was written.
- **It stopped at the namespace.** Per-workload series need
  `prometheusExporter.enableWorkloadMetrics`, which the chart leaves off.

| Section | What it answers |
| --- | --- |
| Top row | Critical and high CVEs, loaded and whole-image; critical and high control failures |
| Image CVEs | Which containers to fix first — a table sorted by critical CVEs in loaded packages, whole-image counts beside them — and a trend per namespace |
| Configuration controls | Which workloads fail which severities, and a trend per namespace |
| Details | The `kubectl` commands below, for going from a table row to the findings |

A `namespace` variable filters every panel. The trends are the part that pays
off later: a step after a sync is a regression a chart or image bump brought in.

Three things the counts cannot tell you:

- **"Loaded" reads 0 until the node-agent has profiled a container**, and a
  zero from that is indistinguishable from a clean container. A row with a high
  whole-image count and zero loaded is more often not yet profiled than safe;
  the summary's `vulnerabilitiesRef.relevant.name` is empty in that case.
- **The CVE IDs are not in Prometheus.** One series per CVE per container would
  be thousands of series for a table better read with `kubectl`.
- **An unscanned image reads 0 in *both* columns**, which is the one reading
  that looks like good news. A zero pair means
  [check for a skipped scan](#an-image-whose-sbom-is-too-large-is-not-scanned-at-all)
  before believing it.

The panels take their datasource from a `datasource` variable restricted to
Prometheus, so the Loki datasource's `isDefault: false` no longer matters to it.
The chart renders no dashboard of its own; the ConfigMap lives in `kubescape`,
next to the component, and Grafana's dashboard sidecar watches every namespace.

## Reading results without Grafana

Findings are stored in an aggregated API server, so they are ordinary
`kubectl` objects. `kubectl get` on a list of them prints names only — the
aggregated API leaves `spec` out of list responses — so fetch objects one at a
time to see counts.

```bash
# Failed controls per namespace (cluster-scoped, named after the namespace)
kubectl get configurationscansummaries authentik -o yaml

# The full finding set for one workload
kubectl get workloadconfigurationscans -n <namespace> \
  -l kubescape.io/workload-name=<workload> -o yaml
```

CVEs take two hops. The per-container summary, named
`<kind>-<workload>-<container>`, points at two manifests in the `kubescape`
namespace: `all`, named after the image, and `relevant`, named after the
workload instance and holding only loaded packages.

```bash
NS=argocd; SUMMARY=deployment-argocd-server-server
REF=$(kubectl get vulnerabilitymanifestsummaries -n $NS $SUMMARY \
  -o jsonpath='{.spec.vulnerabilitiesRef.relevant.name}')   # .all.name for the whole image
kubectl get vulnerabilitymanifests -n kubescape "$REF" -o json | jq -r '
  .spec.payload.matches[]
  | select(.vulnerability.severity == "Critical" or .vulnerability.severity == "High")
  | [.vulnerability.severity, .vulnerability.id, .artifact.name, .artifact.version,
     .vulnerability.fix.state, ((.vulnerability.fix.versions // []) | join(","))]
  | @tsv'
```

A `fix.state` of `fixed` means a newer package exists; with upstream images
that almost always means waiting for, or bumping to, a newer chart.

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
└── grafana-dashboard.yaml   # Our dashboard (the chart ships none)
```
