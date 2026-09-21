---
description: "The kube-prometheus-stack observability setup, accessing Grafana, and adding dashboards."
---

# Monitoring

Full observability stack based on
[kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack):
Prometheus (10-day retention), Grafana, Alertmanager, node-exporter and
kube-state-metrics. The chart's default rule set is good; its default routing
swallows every alert, which is what most of this page is about.

## At a glance

| | |
| --- | --- |
| Namespace | `monitoring` |
| Stage | `08-services`; the CRDs in `01-crds` |
| Depends on | [Rook-Ceph](rook-ceph.md) for Prometheus and Grafana volumes, [Authentik](authentik.md) for the Grafana login |
| If it is down | No alerts and no metrics — and [Kured](kured.md) loses the alert gate it checks before rebooting a node |
| Health check | `kubectl -n monitoring get prometheus,alertmanager`; the `Watchdog` alert should always be firing |
| UI | `monitoring.infra.k8s.wlkr.ch` (Grafana), `prometheus.` and `alertmanager.` |
| Files | `payload/platform/monitoring/`, `payload/platform/prometheus-operator-crds/` |

## Configuration

### CRDs

The `monitoring.coreos.com` CRDs are a separate `prometheus-operator-crds`
Application in `01-crds`, from the prometheus-community chart of that name,
and this chart runs with `crds.enabled: false`. Cilium, cert-manager, External
Secrets, Rook and OpenBao all render ServiceMonitors or PrometheusRules in
stages ahead of `08-services`, and a missing kind fails the sync that
[gates the stage](../architecture/gitops.md). The Application has no resources
finalizer, so deleting it leaves the CRDs, and every ServiceMonitor, Prometheus
and Alertmanager with them, in place.

!!! warning "Two versions that have to agree"
    `prometheus-operator-crds` must carry the same operator version as kube-prometheus-stack; an operator newer than its CRDs can depend on fields they do not define. Renovate bumps them separately: merge the CRD bump first, or both together — see [Maintenance](../development/maintenance.md).

### Alerting

Alertmanager routes to email. The chart's `config` is off
(`alertmanagerSpec.useExistingSecret`) and `alertmanager-config.yaml` renders
the whole `alertmanager.yaml` through ESO into `alertmanagerSpec.configSecret`,
because only the password has a file form (`smtp_auth_password_file`) and the
login and recipient are e-mail addresses that do not belong in Git.

| Setting in `alertmanager-config.yaml` | Why |
| --- | --- |
| `smtp_smarthost` on port 465 | Implicit TLS, which Alertmanager selects from the port; `smtp_require_tls` stays on so a move back to 587 cannot fall to plaintext |
| `smtp_from` equals the login | mailbox.org refuses a sender the login does not own |
| Grouping by `alertname` and `namespace`, shorter repeat for `critical`, resolved notifications sent | One mail per problem, a "back to normal" mail after it |
| `Watchdog` routed to `null` | It fires continuously as proof the pipeline is alive; mailing it would train the recipient to filter the sender |
| Inhibit rules repeated from the chart defaults | A supplied config replaces the whole document, so nothing is inherited |
| Every templated value through `toJson` | A password containing `:`, `#` or a quote cannot break the rendered file |

The three values come from `kv/monitoring/smtp` (`username`, `password`,
`to`); `make bao-secrets` prompts for them, or by hand:

```bash
bao kv put kv/monitoring/smtp username=... password=... to=...
```

For another provider, change `smtp_smarthost` in the template and the three
values in OpenBao.

### Scrape targets

Every `ServiceMonitor`, `PodMonitor` and `PrometheusRule` in every namespace,
through the five `*SelectorNilUsesHelmValues: false` settings: the chart
treats an empty selector as unset and renders `release: kube-prometheus-stack`
in its place, which limits Prometheus to its own targets.

| Target | Made scrapable by | Notes |
| --- | --- | --- |
| kube-scheduler, kube-controller-manager | `bind-address` in `ansible/templates/kubeadm.yaml.j2` (kubeadm binds them to `127.0.0.1`) | HTTPS with a self-signed certificate, so the ServiceMonitors set `insecureSkipVerify`; the ServiceAccount token still authenticates |
| etcd | `listen-metrics-urls` in the same template (kubeadm gives it none) | Plain HTTP on 2381, serving only `/metrics` and `/health`; client and peer APIs stay on mTLS |
| Ceph | `monitoring.enabled` on the `CephCluster` — see [Rook-Ceph](rook-ceph.md#monitoring) | |
| kube-proxy | Not scraped (`kubeProxy.enabled: false`) | Cilium replaces it; the default would keep `KubeProxyDown` firing forever |

!!! note
    A cluster provisioned before the kubeadm change keeps the old flags in its static pod manifests; until those nodes are reprovisioned the three control-plane targets stay down.

### Grafana

| Setting | Why |
| --- | --- |
| `disable_login_form` and `oauth_auto_login` | Opening the URL bounces straight to Authentik and back; `role_attribute_path` maps `grafana-admins` to `Admin`, `grafana-editors` to `Editor`, anyone else to `Viewer` |
| `grafana-oidc.yaml` | The OIDC client is generated into OpenBao and read by both sides — see [Authentik](authentik.md#client-secrets) |
| `grafana.admin.existingSecret`, from `grafana-admin.yaml` | Left unset, the chart generates a new password on every render, so the Secret is always OutOfSync and `checksum/secret` restarts Grafana on every sync. The admin account is the [break-glass path](authentik.md#when-authentik-is-down); never set `grafana.adminPassword` in Git |
| `Recreate` strategy | The dashboard PVC is RWO; under `RollingUpdate` the new pod waits for a volume the old pod releases only once the new one is Ready, parking on `FailedAttachVolume` |
| Prometheus and Grafana uncapped | Both grow with series count and dashboard load, and OOMKilling the thing that reports cluster health is the failure worth avoiding |
| Alertmanager `automountServiceAccountToken: false` | Nothing binds a role to it — the operator talks to the API — so the token would only be a credential in a pod reachable from the Gateway |

### Dashboards

Where a chart can render a dashboard, it does; only charts without one get a
vendored copy, which Renovate does not update — the header of each file names
its upstream tag.

| Component | Dashboards | Source |
| --- | --- | --- |
| [Cilium](cilium.md) | Cilium Metrics, Cilium Operator, four Hubble dashboards | Chart: `dashboards`, `operator.dashboards`, `hubble.metrics.dashboards` |
| [OpenBao](openbao.md) | OpenBao | Chart: `serverTelemetry.grafanaDashboard` |
| [External Secrets](external-secrets.md) | External Secrets Operator | Chart: `grafanaDashboard` |
| [Rook-Ceph](rook-ceph.md) | Ceph Cluster, Ceph - OSD (Single), Ceph - Pools | Vendored: `rook-ceph/grafana-dashboards.yaml` |
| [Alloy](logging.md) | Alloy / Controller, Alloy / Loki Components, Alloy / Resources | Vendored: `logging/grafana-dashboards.yaml` |
| ArgoCD | ArgoCD | Vendored: `argocd-config/argocd-dashboard.yaml` |

## Usage

### Adding a dashboard

Add a ConfigMap labelled `grafana_dashboard: "1"` next to the component it
describes; the sidecar watches every namespace
(`sidecar.dashboards.searchNamespace: ALL`). Prefer the chart's own dashboard
where it has one. A dashboard built in the UI survives restarts on the PVC but
lives nowhere else: not in Git, not on a rebuilt cluster.

### Resetting the Grafana admin password

Grafana reads the password only when it creates its database, so changing
`kv/monitoring/grafana-admin` later does not change what it checks:

```bash
kubectl -n monitoring exec deploy/kube-prometheus-stack-grafana -c grafana -- \
  grafana cli admin reset-admin-password "$PASSWORD"
```

## Health check

```bash
kubectl -n monitoring get prometheus,alertmanager
kubectl -n monitoring get prometheus kube-prometheus-stack-prometheus \
  -o jsonpath='{.spec.serviceMonitorSelector}'   # should print {}
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
# then open http://localhost:9093 and check Status -> Config
```

`Watchdog` proves the pipeline as far as Alertmanager and not one step
further; SMTP auth, spam heuristics and an expired password are unmonitored.
Send yourself a test alert once and confirm it arrives.
