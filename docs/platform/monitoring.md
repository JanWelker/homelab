---
description: "The kube-prometheus-stack observability setup, accessing Grafana, and adding dashboards."
---

# Monitoring

Full observability stack based on [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack).

## Components

- **Prometheus**: Metrics collection and storage with 10-day retention.
- **Grafana**: Dashboards at [https://monitoring.infra.k8s.wlkr.ch](https://monitoring.infra.k8s.wlkr.ch).
- **Alertmanager**: Alert routing and notifications.
- **Node Exporter**: Per-node hardware and OS metrics.
- **kube-state-metrics**: Kubernetes object metrics (pod status, deployments, etc.).

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
monitoring/            # Observability Stack
├── application.yaml   # kube-prometheus-stack (Helm chart)
└── httproute.yaml     # Grafana route
```
