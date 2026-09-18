---
description: "The kube-prometheus-stack observability setup, accessing Grafana, and adding dashboards."
---

# Monitoring

Full observability stack based on [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack).

One chart, five components, and roughly a hundred alerting rules you did not write and should read at least once. The default rule set is genuinely good; the default *routing* is not, which is what most of this page is about.

## At a glance

| | |
| --- | --- |
| Namespace | `monitoring` |
| Sync wave | `1` |
| Depends on | [Rook-Ceph](rook-ceph.md) for Prometheus and Grafana volumes, [Authentik](authentik.md) for the Grafana login |
| If it is down | No alerts and no metrics — and [Kured](kured.md) loses the alert gate it checks before rebooting a node |
| Health check | `kubectl -n monitoring get prometheus,alertmanager`; the `Watchdog` alert should always be firing |
| UI | `monitoring.infra.k8s.wlkr.ch` (Grafana), `prometheus.` and `alertmanager.` |

## Components

- **Prometheus**: Metrics collection and storage with 10-day retention.
- **Grafana**: Dashboards at [https://monitoring.infra.k8s.wlkr.ch](https://monitoring.infra.k8s.wlkr.ch).
- **Alertmanager**: Routes to an [email receiver](#alerting).
- **Node Exporter**: Per-node hardware and OS metrics.
- **kube-state-metrics**: Kubernetes object metrics (pod status, deployments, etc.).

## CRDs

The chart owns the `monitoring.coreos.com` CRDs -- `crds.enabled`, plus an
upgrade job that re-applies them on every chart bump -- but it is not the first
thing in the cluster that needs them. Cilium and cert-manager both render
ServiceMonitors and sync waves ahead of this stack. Neither is blocked for good:
both carry `SkipDryRunOnMissingResource` and a `retry`, so on a new cluster they
apply everything else, fail on the missing kind, and succeed once this stack
has installed the CRDs. See [Cilium](cilium.md#installation).

## Alerting

Alertmanager routes to email. The chart's default is a `null` receiver that
swallows every alert, which is worse than not deploying it at all: the stack
looks configured, the dashboards are full of data, the rules evaluate correctly,
and every single alert goes into a bin. Monitoring you believe in but that
cannot reach you is strictly more dangerous than no monitoring, because it buys
false confidence. So the route and receiver below are set explicitly.

| Property | Value |
| --- | --- |
| Receiver | `email`, to the address in `kv/monitoring/smtp` |
| Smarthost | `smtp.mailbox.org:465`, implicit TLS |
| Login, sender, password | `kv/monitoring/smtp` in OpenBao; the sender is the login |
| Grouping | By `alertname` and `namespace` |
| Repeat | 12h, or 3h for `severity = critical` |
| Resolved | Sent — a "back to normal" mail follows the alert |

`Watchdog` is routed to `null` on purpose. It fires continuously by design, as
proof the pipeline is alive; mailing it every twelve hours would train the
recipient to filter the sender — and a filtered alert sender is how outages get
missed. Alert fatigue is not a personal failing, it is a design outcome, and the
design is under your control.

### Why the whole config comes from OpenBao

Only the password is a credential, but the login and the recipient are email
addresses, and those are not published in this repository. Alertmanager can
read the password from a file (`smtp_auth_password_file`) and nothing else:
there is no file form for the username, `from` or `to`. A config rendered by the
chart would have to spell them out.

So the chart's `config` is off (`alertmanagerSpec.useExistingSecret`), and
`alertmanager-config.yaml` renders the complete `alertmanager.yaml` with ESO
into the Secret named by `alertmanagerSpec.configSecret`. Routes, receivers and
inhibit rules are still written out in that file's template, so they stay
reviewable; only the three values below are filled in from OpenBao. The inhibit
rules are the chart's defaults, repeated: a supplied config replaces the whole
document, so nothing is inherited. Each templated value goes through `toJson`,
which makes it a quoted YAML string — a password containing `:`, `#` or a quote
cannot break the rendered file.

| Key in `kv/monitoring/smtp` | Used as |
| --- | --- |
| `username` | `smtp_auth_username` and `smtp_from` — mailbox.org refuses a sender the login does not own |
| `password` | `smtp_auth_password`, the account or an app password |
| `to` | The `email` receiver's recipient |

`make bao-secrets` prompts for all three. By hand:

```bash
bao kv put kv/monitoring/smtp username=... password=... to=...
```

Port 465 is implicit TLS, which Alertmanager selects from the port number;
`smtp_require_tls` only applies to STARTTLS and stays on so a move back to 587
cannot fall to plaintext. For a different mail provider, change
`smtp_smarthost` in the template and the three values in OpenBao.

### Checking it works

`Watchdog` proves the pipeline as far as Alertmanager and not one step further.
Everything between Alertmanager and your inbox — SMTP auth, the provider's spam
heuristics, a password that expired — is unmonitored. To prove delivery, inspect
what Alertmanager thinks it is doing, and then, once, actually send yourself a
test alert and confirm it arrives:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
# then open http://localhost:9093 and check Status -> Config
```

## What is scraped

Every `ServiceMonitor`, `PodMonitor` and `PrometheusRule` in every namespace.
That takes the five `*SelectorNilUsesHelmValues: false` settings in
`application.yaml`, not the empty selectors they replaced: the chart treats
`serviceMonitorSelector: {}` as unset and renders
`release: kube-prometheus-stack` in its place, which quietly limits Prometheus
to this chart's own targets. Check the rendered result, not the values:

```bash
kubectl -n monitoring get prometheus kube-prometheus-stack-prometheus \
  -o jsonpath='{.spec.serviceMonitorSelector}'   # should print {}
```

Ceph reaches Prometheus once `monitoring.enabled` is `true` on the
`CephCluster` — that switch lives in [Rook-Ceph](rook-ceph.md) rather than here.

kube-scheduler, kube-controller-manager and etcd are scraped too, but only
because the provisioning config binds them somewhere reachable. kubeadm binds
the first two to `127.0.0.1` and gives etcd no metrics listener, which leaves
their ServiceMonitors permanently down and `KubeSchedulerDown`,
`KubeControllerManagerDown` and the etcd alerts permanently firing. Three alerts
that are always red is how a team learns to ignore red, so this is worth fixing
rather than silencing. The
`bind-address` and `listen-metrics-urls` arguments in
`ansible/templates/kubeadm.yaml.j2` are what make these targets real.

The scheduler and controller-manager serve metrics over HTTPS with a
self-signed certificate for the node address, so their ServiceMonitors set
`insecureSkipVerify` — the scrape still authenticates with the ServiceAccount
token. etcd's metrics listener on port 2381 is plain HTTP and serves only
`/metrics` and `/health`; the client and peer APIs stay on their mTLS
listeners.

!!! note
    A cluster provisioned before that change keeps the old flags — they are
    baked into the static pod manifests in `/etc/kubernetes/manifests/`. Until
    those nodes are reprovisioned or the manifests edited, expect those three
    targets to be down.

kube-proxy is not scraped at all (`kubeProxy.enabled: false`): Cilium replaces
it, so there is nothing to scrape, and leaving the chart's default on keeps
`KubeProxyDown` firing forever.

## Accessing Grafana

Grafana is exposed via the `infra-gateway` at `monitoring.infra.k8s.wlkr.ch`,
behind [Authentik](authentik.md). There is no login form: `disable_login_form`
and `oauth_auto_login` are both set, so opening the URL bounces straight to
Authentik and back.

Your role comes from group membership, evaluated by `role_attribute_path`:

| Authentik group | Grafana role |
| --- | --- |
| `grafana-admins` | `Admin` |
| `grafana-editors` | `Editor` |
| neither | `Viewer` |

The OIDC client ID and secret are not minted by Grafana. They are generated once
into OpenBao and materialised as the `grafana-oidc` Secret by
`grafana-oidc.yaml`, then read by both sides — which is what lets a rebuilt
Grafana and a rebuilt Authentik still agree with each other. See
[Authentik &rarr; Client secrets are generated up front](authentik.md#client-secrets-are-generated-up-front).

!!! note "The admin account still exists"
    Disabling the login form hides it, it does not remove it. The local admin remains reachable through the API, which is the break-glass path for when Authentik is down — see [When Authentik is down](authentik.md#when-authentik-is-down). Do not set `grafana.adminPassword` in `application.yaml` to make that easier; it would commit a credential to Git.

### The admin password

The admin password lives in OpenBao at `kv/monitoring/grafana-admin` and reaches
Grafana through the `grafana-admin` Secret, rendered by `grafana-admin.yaml` and
named in `grafana.admin.existingSecret`. `make bao-secrets` generates it.

Leaving `existingSecret` unset is not harmless. The chart then generates a new
random password on every render, so ArgoCD always sees its Secret as OutOfSync,
and the `checksum/secret` annotation on the Deployment changes with it: every
sync restarts Grafana.

Grafana reads the password only when it creates its database, and the database
lives on the PVC. Changing the value in OpenBao later does not change the
password Grafana checks. Reset it to match:

```bash
kubectl -n monitoring exec deploy/kube-prometheus-stack-grafana -c grafana -- \
  grafana cli admin reset-admin-password "$PASSWORD"
```

## Dashboards

Besides the Kubernetes dashboards kube-prometheus-stack ships, each component
brings its own, from upstream. Where the chart can render one, the chart does;
only charts without a dashboard get a vendored copy. Renovate does not update
vendored copies; the header of each file names the upstream tag it came from.

| Component | Dashboards | Source |
| --- | --- | --- |
| [Cilium](cilium.md) | Cilium Metrics, Cilium Operator, four Hubble dashboards | Chart: `dashboards`, `operator.dashboards`, `hubble.metrics.dashboards` |
| [OpenBao](openbao.md) | OpenBao | Chart: `serverTelemetry.grafanaDashboard` |
| [External Secrets](external-secrets.md) | External Secrets Operator | Chart: `grafanaDashboard` |
| [Kubescape](kubescape.md) | Kubescape | Written for this cluster: `kubescape/grafana-dashboard.yaml` |
| [Rook-Ceph](rook-ceph.md) | Ceph Cluster, Ceph - OSD (Single), Ceph - Pools | Vendored from Rook: `rook-ceph/grafana-dashboards.yaml` |
| [Alloy](logging.md) | Alloy / Controller, Alloy / Loki Components, Alloy / Resources | Vendored from the Alloy mixin: `logging/grafana-dashboards.yaml` |
| ArgoCD | ArgoCD | Vendored from Argo CD: `payload/argocd/argocd-dashboard.yaml` |

## Adding a Dashboard

Grafana is configured with persistent storage (Rook-Ceph). Dashboards can be added:

- **Via the UI**: Changes persist across restarts because of the PVC.
- **Via ConfigMap**: Add a ConfigMap with the label `grafana_dashboard: "1"` next to the component it describes. The sidecar watches every namespace (`sidecar.dashboards.searchNamespace: ALL`) and imports it.
- **Via the component's chart**: Many charts can render that ConfigMap themselves. Prefer it over a vendored copy, since it follows the chart's version.

Prefer the ConfigMap for anything you would be annoyed to lose. A dashboard
built in the UI lives in one PVC and nowhere else — it is not in Git, Velero is
its only copy, and it will not follow you to a rebuilt cluster. Every
organisation has exactly one irreplaceable dashboard that someone made in the UI
four years ago, and nobody knows how to recreate it.

## Resources and rollout

Memory limits are sized at roughly 2.5 times the measured peak working set.
The Grafana sidecars peaked at 89Mi and 92Mi; the operator, its config
reloader, kube-state-metrics, node-exporter and Alertmanager each have their
own limits in `application.yaml`.

Prometheus and Grafana are deliberately uncapped. Prometheus peaked at 1501Mi
and grows with series count and retention; Grafana peaked at 680Mi, which is
high enough for a dashboard renderer that it wants explaining before it is
capped. Neither can be sized from a day of steady state, and OOMKilling the
thing that tells you the cluster is unhealthy is the specific failure worth
avoiding.

Grafana uses the `Recreate` deployment strategy. Its dashboard PVC is
ReadWriteOnce, and under the chart's default `RollingUpdate` the new pod waits
for a volume the old pod releases only once the new one is Ready — a rollout
parks at `FailedAttachVolume: Volume is already used by pod(s)` until someone
deletes a pod by hand. A few seconds of downtime is the price of a
single-replica, single-volume Grafana.

Alertmanager does not mount a ServiceAccount token
(`automountServiceAccountToken: false`). No RoleBinding or ClusterRoleBinding
names it — the operator talks to the API, not Alertmanager — so the token would
buy nothing and leave a working credential in a pod reachable from the Gateway.

## Directory Structure

```text
monitoring/                  # Observability Stack
├── application.yaml         # kube-prometheus-stack (Helm chart)
├── grafana-admin.yaml       # ExternalSecret: Grafana admin password
├── grafana-oidc.yaml        # ExternalSecret: Grafana OIDC client
├── alertmanager-config.yaml # ExternalSecret: the whole Alertmanager config
└── httproute.yaml           # Grafana route
```
