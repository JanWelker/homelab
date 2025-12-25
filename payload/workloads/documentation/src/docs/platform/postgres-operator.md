# Zalando Postgres Operator

The **Zalando Postgres Operator** is deployed to manage High-Availability PostgreSQL clusters within the platform.

- **Source Code**: [GitHub](https://github.com/zalando/postgres-operator)
- **Deployment**: ArgoCD Application `payload/platform/postgres-operator`

## Usage

To request a new Postgres cluster, define a `postgresql` resource (Kind: `postgresql.acid.zalan.do/v1`).

### Example

```yaml
apiVersion: postgresql.acid.zalan.do/v1
kind: postgresql
metadata:
  name: my-app-db
  namespace: my-app
spec:
  teamId: "my-team"
  volume:
    size: 5Gi
  numberOfInstances: 2
  users:
    zalando:  # database owner
    - superuser
    - createdb
  databases:
    zalando: zalando  # dbname: owner
  postgresql:
    version: "16"
```

## Workloads

Currently used by:

- **Infisical**: For storing secrets and configuration state.
