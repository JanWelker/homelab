# wichteln

A Secret Santa application.

## Components

- **Deployment**: `payload/workloads/wichteln/deployment.yaml`
- **PostgreSQL**: `payload/workloads/wichteln/postgres.yaml` - Database dependency (likely Zalando Operator).
- **Service**: `payload/workloads/wichteln/service.yaml`
- **HTTPRoute**: `payload/workloads/wichteln/httproute.yaml`
- **NetworkPolicy**: `payload/workloads/wichteln/networkpolicy.yaml`

## Dependencies

Requires a running PostgreSQL instance managed by the Postgres Operator.
