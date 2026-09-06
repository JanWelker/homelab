---
description: "The kube-prometheus-stack observability setup, accessing Grafana, and adding dashboards."
---

# Monitoring

Full observability stack based on [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack).

One chart, five components, and roughly a hundred alerting rules you did not write and should read at least once. The default rule set is genuinely good; the default *routing* is not, which is what most of this page is about.

## Components

- **Prometheus**: Metrics collection and storage with 10-day retention.
- **Grafana**: Dashboards at [https://monitoring.infra.k8s.wlkr.ch](https://monitoring.infra.k8s.wlkr.ch).
- **Alertmanager**: Routes to an [email receiver](#alerting).
- **Node Exporter**: Per-node hardware and OS metrics.
- **kube-state-metrics**: Kubernetes object metrics (pod status, deployments, etc.).

## Alerting

Alertmanager routes to email. The chart's default is a `null` receiver that
swallows every alert, which is worse than not deploying it at all: the stack
looks configured, the dashboards are full of data, the rules evaluate correctly,
and every single alert goes into a bin. Monitoring you believe in but that
cannot reach you is strictly more dangerous than no monitoring, because it buys
false confidence. So the route and receiver below are set explicitly.

| Property | Value |
| --- | --- |
| Receiver | `email`, to `jan@wlkr.ch` |
| Smarthost | `smtp.wlkr.ch:587`, STARTTLS |
| Password | `kv/monitoring/smtp` in OpenBao, mounted as a file |
| Grouping | By `alertname` and `namespace` |
| Repeat | 12h, or 3h for `severity = critical` |
| Resolved | Sent — a "back to normal" mail follows the alert |

`Watchdog` is routed to `null` on purpose. It fires continuously by design, as
proof the pipeline is alive; mailing it every twelve hours would train the
recipient to filter the sender — and a filtered alert sender is how outages get
missed. Alert fatigue is not a personal failing, it is a design outcome, and the
design is under your control.

### Why only the password is a secret

Alertmanager supports `smtp_auth_password_file` but has no equivalent for the
username, so the smarthost, from address and username stay in
`application.yaml`. None of them is sensitive. The password is rendered from
OpenBao by `smtp-credentials.yaml` and mounted at
`/etc/alertmanager/secrets/alertmanager-smtp/password` through
`alertmanagerSpec.secrets`.

Set it before expecting mail:

```bash
bao kv put kv/monitoring/smtp password="$SMTP_PASSWORD"
```

For a different mail provider, the three values in `global:` are the only ones
to change.

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

!!! note
    A cluster provisioned before that change keeps the old flags — they are
    baked into the static pod manifests in `/etc/kubernetes/manifests/`. Until
    those nodes are reprovisioned or the manifests edited, expect those three
    targets to be down.

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
    Disabling the login form hides it, it does not remove it. The local admin remains reachable through the API, which is the break-glass path for when Authentik is down — see [When Authentik is down](authentik.md#when-authentik-is-down). Do not set `grafana.adminPassword` in `application.yaml` to make that easier; it would commit a credential to Git for a path that already works without one.

## Adding a Dashboard

Grafana is configured with persistent storage (Rook-Ceph). Dashboards can be added:

- **Via the UI**: Changes persist across restarts because of the PVC.
- **Via ConfigMap**: Add a ConfigMap with the label `grafana_dashboard: "1"` to the `monitoring` namespace and it will be auto-imported.

Prefer the ConfigMap for anything you would be annoyed to lose. A dashboard
built in the UI lives in one PVC and nowhere else — it is not in Git, Velero is
its only copy, and it will not follow you to a rebuilt cluster. Every
organisation has exactly one irreplaceable dashboard that someone made in the UI
four years ago, and nobody knows how to recreate it.

## Directory Structure

```text
monitoring/                  # Observability Stack
├── application.yaml         # kube-prometheus-stack (Helm chart)
├── smtp-credentials.yaml    # ExternalSecret: Alertmanager SMTP password
└── httproute.yaml           # Grafana route
```
