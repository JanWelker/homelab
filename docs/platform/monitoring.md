---
description: "The kube-prometheus-stack observability setup, accessing Grafana, and adding dashboards."
---

# Monitoring

Full observability stack based on [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack).

## Components

- **Prometheus**: Metrics collection and storage with 10-day retention.
- **Grafana**: Dashboards at [https://monitoring.infra.k8s.wlkr.ch](https://monitoring.infra.k8s.wlkr.ch).
- **Alertmanager**: Routes to an [email receiver](#alerting).
- **Node Exporter**: Per-node hardware and OS metrics.
- **kube-state-metrics**: Kubernetes object metrics (pod status, deployments, etc.).

## Alerting

Alertmanager previously had persistent storage, no route and no receiver, so
every alert reached the chart's `null` receiver. That is worse than not
deploying it: the stack looked configured and could not tell anyone anything.

It now routes to email.

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
recipient to filter the sender.

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

`Watchdog` proves the pipeline as far as Alertmanager. To prove delivery,
inspect what Alertmanager thinks it is doing:

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
`KubeControllerManagerDown` and the etcd alerts permanently firing. The
`bind-address` and `listen-metrics-urls` arguments in
`ansible/templates/kubeadm.yaml.j2` are what make these targets real.

!!! note
    A cluster provisioned before that change keeps the old flags — they are
    baked into the static pod manifests in `/etc/kubernetes/manifests/`. Until
    those nodes are reprovisioned or the manifests edited, expect those three
    targets to be down.

## Accessing Grafana

Grafana is exposed via the `infra-gateway` at `monitoring.infra.k8s.wlkr.ch`.

Log in as `admin`. This repository does not set `grafana.adminPassword` in
`application.yaml`, so the chart's own default applies. Read the password that
is actually in effect from the Secret the chart creates:

```bash
kubectl get secret -n monitoring kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d
```

!!! note
    The Grafana credentials are not yet managed through
    [OpenBao](openbao.md). Until they are, changing the password means setting
    `grafana.adminPassword` in `application.yaml` — which would commit it to
    Git. Prefer an [ExternalSecret](external-secrets.md) and reference it with
    `grafana.admin.existingSecret`.

## Adding a Dashboard

Grafana is configured with persistent storage (Rook-Ceph). Dashboards can be added:

- **Via the UI**: Changes persist across restarts because of the PVC.
- **Via ConfigMap**: Add a ConfigMap with the label `grafana_dashboard: "1"` to the `monitoring` namespace and it will be auto-imported.

## Directory Structure

```text
monitoring/                  # Observability Stack
├── application.yaml         # kube-prometheus-stack (Helm chart)
├── smtp-credentials.yaml    # ExternalSecret: Alertmanager SMTP password
└── httproute.yaml           # Grafana route
```
