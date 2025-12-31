# monitoring

Full observability stack.

## Components

- **Prometheus**: Metrics collection with 10-day retention.
- **Grafana**: Dashboards at `monitoring.infra.k8s.wlkr.ch`.
- **Alertmanager**: Alert routing and notifications.

## Directory Structure

```text
monitoring/            # Observability Stack
├── application.yaml   # kube-prometheus-stack
└── httproute.yaml     # Grafana route
```
