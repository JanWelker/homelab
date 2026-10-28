---
description: "HolmesGPT investigates alerts and pods with the cluster's own observability data, runs one health check a day, and opens pull requests for what it finds."
---

# HolmesGPT

[HolmesGPT](https://github.com/HolmesGPT/holmesgpt) is an agent that
investigates an incident the way an operator would: it reads pods, events and
logs through the API server, queries Prometheus and Loki, asks Argo CD what a
Git revision was supposed to deploy, and writes up a root cause. Its operator
runs a `ScheduledHealthCheck` once a day and keeps the verdict in the
resource's status. Fixes go through GitHub as pull requests, the only way
anything changes here.

The [agent sessions](https://homelab-apps.wlkr.ch/claude-agents/) remain the
place for interactive work on a repository; Holmes is the thing that looks
without being asked.

## At a glance

| | |
| --- | --- |
| Namespace | `holmesgpt` |
| Depends on | [OpenBao](openbao.md) for its four credentials; [Monitoring](monitoring.md), [Logging](logging.md) and [ArgoCD](argocd.md) as data sources |
| If it is down | Nothing visible: the daily check does not run and `holmes` on the laptop gets no answer |
| Health check | `kubectl -n holmesgpt get shc,hc` &rarr; yesterday's check `pass` or `fail`, not `error` |
| Files | `payload/platform/holmesgpt/`, `payload/platform/security/network-policies/holmesgpt.yaml`; the metrics in `payload/platform/monitoring/application.yaml` |

## Configuration

The values are in `payload/platform/holmesgpt/application.yaml`; the
reasoning:

| Setting | Why |
| --- | --- |
| `enableTelemetry: false`, `sentryDSN: ""` | Telemetry is off in config in every component here, not merely blocked by policy |
| `runAsUser: 65534`, `HOME=/tmp` | The image declares no user and the namespace enforces `restricted`. The root filesystem is read-only already, so the only directory the process ever writes is the `/tmp` the chart mounts; `HOME` points there because `/root` is unreadable to anyone else and git looks for `~/.gitconfig` |
| `crdPermissions` | The chart's ClusterRole lists every CRD it knows. Only the groups this cluster has are left on; the rest would be rules that grant nothing and read as if they did |
| `customClusterRoleRules` | The chart's ClusterRole covers the kinds every cluster has plus the CRD groups in `crdPermissions`; the platform's own, CloudNativePG, cert-manager, Rook, Trivy, Cilium, snapshots and the HealthChecks, are added read-only, the same list as the [agent read role](security-policies.md#agent-rbac). The first daily check reported CloudNativePG backups as forbidden |
| `modelList` with `envRef:` | Sonnet as the default, Haiku selectable per request for a cheap question. The key is read from the env var the ExternalSecret below provides, so the values carry no secret |
| `TOOL_MEMORY_LIMIT_MB` | Every tool command runs under `ulimit -v`, 800 MB on amd64 by default. `argocd app list` and `kubectl get pods -A -o json` are Go binaries whose address space alone exceeds that, so the check reported them as out of memory; both pass at 1.5 GB, and 2 GB leaves room for a larger cluster. It bounds address space, not the pod's memory, which `resources` still caps |
| `TEMPERATURE=none` | Claude 5.5 answers `temperature is deprecated for this model` to any request that carries one, and Holmes sends its own default unless told to send none; a `temperature` in `modelList` would be sent as well |
| `toolsets` | On: Kubernetes, Prometheus, Loki (direct, single-tenant), Grafana's dashboard API and the `argocd` CLI. Off, against the chart's defaults: `internet` (fetches any URL), `bash` (runs shell commands) and `robusta` (phones the vendor's platform). `connectivity_check` is off for the same reason as `internet`. The `helm` toolset is pointless here, since Argo CD renders charts without a Helm release, and the `cilium` one needs a CLI the image does not ship |
| `ARGOCD_SERVER` on the plaintext port | The server runs with `--insecure` and TLS ends at the Gateway, so the in-cluster Service carries gRPC as it is; no `pods/portforward` permission and no trip through the Gateway |
| `mcp_servers.github` | GitHub's own hosted MCP server, not the vendor-built wrapper image the chart offers. `X-MCP-Toolsets` limits it to repositories, issues, pull requests and Actions; the instructions tell Holmes which repository holds what and that every change is a pull request against `main` |
| `operator.enabled` | Runs the `ScheduledHealthCheck`; upstream calls it alpha, and a check is at least one model call, so there is one a day |

### Secrets

One ExternalSecret, `holmesgpt-env`, reads four paths and renders the env
vars the chart's templating expects. `make bao-secrets` writes all four,
empty until the value exists: a missing path fails the ExternalSecret and the
pod never starts, an empty value only disables that toolset.

| Path | Key | How to get the value |
| --- | --- | --- |
| `kv/holmesgpt/anthropic` | `api-key` | An API key from the Anthropic console, on its own workspace so its spend is visible |
| `kv/holmesgpt/github` | `token` | A fine-grained PAT, 90 days, on `homelab`, `homelab-apps`, `flowscape`, `knead-time`, `fest.wollbi.ch` and `advent.wollbi.ch`: Contents, Pull requests and Issues read-write, Actions read; see [Pull requests](#pull-requests) |
| `kv/holmesgpt/argocd` | `token` | `argocd account generate-token --account holmesgpt --expires-in 90d`, logged in through Authentik. The account has `apiKey` only and `role:readonly`, like the [agent account](argocd.md#agent-account) |
| `kv/holmesgpt/grafana` | `token` | A service account with the `Viewer` role under **Administration → Service accounts**, created by a `grafana-admins` member, and a token on it |

After a value changes, pull it through before the hourly refresh and restart
the pod, which reads its env once:

```bash
kubectl -n holmesgpt annotate externalsecret holmesgpt-env force-sync=$(date +%s) --overwrite
kubectl -n holmesgpt rollout restart deploy/holmesgpt-holmes
```

### Daily check

`scheduled-health-check.yaml` asks one question at 05:00 UTC, after the
nightly backups: nodes, pods, Argo CD Applications, firing alerts, last
night's Velero and CloudNativePG backups, certificates expiring within two
weeks, and Ceph health. `mode: monitor` keeps the result in the resource's
status and sends nothing itself: the operator's destinations are Slack,
PagerDuty and a webhook in its own format, none of which is the mail that
carries every other alert here. The verdict reaches it as a metric instead.
kube-state-metrics renders each HealthCheck's `status.result` and
`status.completionTime` through its
[custom resource state config](monitoring.md#scrape-targets), and
`prometheusrule.yaml` turns the newest scheduled check into
`HolmesDailyCheckFailed` on `fail` or `error`, plus `HolmesDailyCheckStale`
when no check has completed in 36 hours. Both are `warning`, so they ride the
daily mail with the rest; the mail names the resource, the report stays in
it. A manual HealthCheck never alerts: the rule selects on the
`holmesgpt.dev/scheduled-by` label the operator sets.

### Pull requests

Holmes has no clone and no git; it opens a pull request through the GitHub
MCP server's tools, branch and commits included, with the token above. The
token is the blast radius: it can write to the listed repositories and
nothing else, and every repository's ruleset requires the `ci-ok` check
before a merge, so a pull request it opens is reviewed like any other.

## Usage

The API is not routed; reach it with a port-forward and the `holmes` CLI
from the laptop, or from the `homelab` agent session:

```bash
kubectl -n holmesgpt port-forward svc/holmesgpt-holmes 5050:80
curl -s localhost:5050/api/chat -H 'content-type: application/json' \
  -d '{"ask": "why is the velero backup from last night partially failed?"}' | jq -r .analysis
```

A one-off question with the operator:

```bash
kubectl -n holmesgpt create -f - <<'YAML'
apiVersion: holmesgpt.dev/v1alpha1
kind: HealthCheck
metadata:
  generateName: ask-
spec:
  query: "Is the nextcloud namespace healthy?"
  timeout: 300
YAML
kubectl -n holmesgpt get hc -w
```

Yesterday's verdict and its reasoning:

```bash
kubectl -n holmesgpt get hc -o custom-columns=NAME:.metadata.name,RESULT:.status.result,MESSAGE:.status.message
kubectl -n holmesgpt get hc <name> -o jsonpath='{.status.rationale}'
```

## Health check

```bash
kubectl -n holmesgpt get pods,shc,hc
kubectl -n holmesgpt logs deploy/holmesgpt-holmes --tail=50 | grep -i "toolset\|error"
kubectl -n holmesgpt get externalsecret holmesgpt-env
```

A toolset whose prerequisite failed is logged at startup with the reason:
an empty token, or a policy that does not admit the call.

## Pitfalls

- **A toolset that is switched on needs a rule in the network policy.** The
  `holmes` policy admits exactly the data sources the values enable; a new
  toolset with no rule shows up as a timeout in Holmes's answer, not as an
  error, and as a dropped flow in Hubble.
- **Every check costs a model call.** The daily schedule is the budget;
  shorten it only after a month of the spend on the Anthropic console.
- **The chart's defaults are open.** `internet`, `bash` and `robusta` are on
  upstream; a Renovate bump never turns them back on, since the values pin
  them off, but a new toolset the chart adds arrives enabled if upstream
  defaults it so. Read the chart's `values.yaml` diff on a minor bump.
- **The operator is alpha.** A breaking change in the CRDs arrives through a
  Renovate minor; `kubectl -n holmesgpt get shc daily` after the merge.
