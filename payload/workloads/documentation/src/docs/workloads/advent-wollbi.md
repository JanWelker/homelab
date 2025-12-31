# advent-wollbi

A custom application for the Advent season.

## Components

- **Deployment**: `payload/workloads/advent-wollbi/deployment.yaml`
- **CronJob**: `payload/workloads/advent-wollbi/cronjob.yaml` - Suggests scheduled tasks.
- **Service**: `payload/workloads/advent-wollbi/service.yaml`
- **HTTPRoute**: `payload/workloads/advent-wollbi/httproute.yaml` - Exposes the application via Gateway API.
- **NetworkPolicy**: `payload/workloads/advent-wollbi/networkpolicy.yaml` - Security restrictions.
- **PVC**: `payload/workloads/advent-wollbi/pvc.yaml` - Persistent storage.

## Access

The application should be accessible via the configured HTTPRoute host.
