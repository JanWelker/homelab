# Infisical Secrets Management

[Infisical](https://infisical.com) is the deployed secrets management platform for the cluster, reachable at `https://secrets.infra.k8s.wlkr.ch`.

It provides a user-friendly Web UI for managing secrets, certificates, and app configurations.

## Architecture

- **Deployment**: ArgoCD Application `payload/platform/infisical`.
- **Database**: High-Availability Postgres Cluster managed by `postgres-operator` (Zalando).
- **Ingress**: Exposed via Gateway API (`HTTPRoute`) through the `apps-gateway`.
- **Authentication**: Initial login requires creation of the first admin account.

## Setup

The installation requires seeding initial encryption keys before the application can start.

### One-Time Initialization

Run the following make command to create the necessary namespace and generate secure random keys:

```bash
make install-infisical
```

This will create the `infisical-secrets` Kubernetes Secret containing `ENCRYPTION_KEY` and `AUTH_SECRET`.

## Access

1. Navigate to `https://secrets.infra.k8s.wlkr.ch`.
2. If this is the first time accessing, follow the on-screen prompts to create the **Root Admin** account.
3. Configure your projects and environments.

## External Secrets Integration

Infisical integrates natively with the **External Secrets Operator**. To sync secrets from Infisical to your workloads:

1. Create an `InfisicalSecretStore` or `ClusterSecretStore`.
2. Create `ExternalSecret` resources referencing your Infisical project.

*(Refer to External Secrets documentation for CRD specifics)*.
