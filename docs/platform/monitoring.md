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

The default admin credentials are configured in the Helm values (`application.yaml`). Check the `grafana.adminPassword` field or the generated secret in the `monitoring` namespace:

```bash
kubectl get secret -n monitoring kube-prometheus-stack-grafana \
  -o jsonpath='{.data.admin-password}' | base64 -d
```

## Adding a Dashboard

Grafana is configured with persistent storage (Rook-Ceph). Dashboards can be added:

- **Via the UI**: Changes persist across restarts because of the PVC.
- **Via ConfigMap**: Add a ConfigMap with the label `grafana_dashboard: "1"` to the `monitoring` namespace and it will be auto-imported.

## Directory Structure

```text
monitoring/            # Observability Stack
├── application.yaml   # kube-prometheus-stack (v82.15.1)
└── httproute.yaml     # Grafana route
```
