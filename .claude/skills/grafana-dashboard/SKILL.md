---
name: grafana-dashboard
description: Find, add, fix or rewrite a Grafana dashboard on this cluster. Use when the user says "is there a dashboard for Tetragon", "check if there are grafana dashboards for these apps", "the graphs are useless like this, make it unique CVEs per namespace", "make the dashboard more useful", "all my grafana dashboards disappeared", "the numbers in the dashboard are not sinking", or asks for a Grafana panel, PromQL or LogQL expression. Covers how dashboards are provisioned here, chart-shipped versus vendored versus written-here, evaluating every expression against Prometheus before committing, and the counting rules for per-container metrics.
---

# grafana-dashboard

Dashboards are Git objects. One built in the UI lives on the Grafana PVC and
nowhere else. The provisioning rules are in `docs/platform/monitoring.md`
under Dashboards; this skill is the procedure.

## 1. Prefer the chart's own dashboard

Before writing anything, check whether the component's chart renders one:
grep the chart's values for `dashboard`, `grafanaDashboard`, `dashboards`.
If it does, turn it on in `application.yaml` and stop. The monitoring page
lists which components use which source; a dashboard that exists on
grafana.com under the project's name is the next choice, vendored into a
ConfigMap with the upstream tag in the file header so Renovate's silence is
documented.

Write a dashboard here only when upstream ships none. Say so in the file's
first comment line, as `tetragon/grafana-dashboards.yaml` does.

## 2. Where it lives

A `ConfigMap` labelled `grafana_dashboard: "1"` in the component's own
directory under `payload/platform/<component>/` or the workload's directory
in `homelab-apps`, in the component's namespace. The sidecar watches all
namespaces. One dashboard per ConfigMap key, JSON as a block scalar.

Grafana's admin password comes from OpenBao; a dashboard that "disappeared"
after a chart bump is a ConfigMap the sidecar stopped matching, or the PVC
that a UI-built dashboard lived on. Check `kubectl get cm -A -l grafana_dashboard=1`
first.

## 3. Evaluate every expression before it is committed

Prometheus answers through the API server proxy, no port-forward:

```bash
Q='sum(up)'
kubectl get --raw "/api/v1/namespaces/monitoring/services/kube-prometheus-stack-prometheus:9090/proxy/api/v1/query?query=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$Q")" | jq '.data.result[:3]'
```

Loki the same way through its gateway service in the `logging` namespace
with `/loki/api/v1/query`. A panel goes into the PR only after its query
returned rows here. Check the label names against `series` or
`label/<name>/values`; a query on a label that does not exist returns
empty without an error and looks like a clean cluster.

## 4. Counting rules that bit before

- **Trivy and other per-workload reports are one row per container per
  ReplicaSet revision.** Six criticals in two containers of two revisions
  read as 24. Count unique `image_digest` on workloads that have pods, and
  say in the panel title what is counted.
- **Split severities into separate panels** rather than stacking; a
  stacked graph hides whether critical moved.
- **Rate over counter, not raw counter**, and a `for`-style window that
  matches the scrape interval, or the panel shows saw-teeth.
- **Numbers that will not sink** are usually the old revision's report
  still inside its TTL, or a scan that started succeeding and added
  findings that were always there. Explain before changing the query.

## 5. Ship

One PR per dashboard through `pr-batch`, with the panel list and one
screenshot or the evaluated query output in the body. Update the table in
`docs/platform/monitoring.md` when a component gains or changes its source.
