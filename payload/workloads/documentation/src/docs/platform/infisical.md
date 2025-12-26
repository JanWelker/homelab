# Infisical

Infisical is an open-source secret management platform that we use to manage secrets across our infrastructure. It syncs secrets from the Infisical platform to Kubernetes Secrets.

## Architecture

The Infisical deployment in this cluster consists of:

- **Infisical Backend**: The core service handling API requests.
- **Redis**: A standalone Redis instance for caching and queuing.
- **PostgreSQL**: An external PostgreSQL cluster managed by the `zalando-postgres-operator`. We **disable** the Helm chart's built-in PostgreSQL to leverage the robustness and features of the operator-managed cluster.

## Installation

Infisical is deployed via ArgoCD using the official Helm chart.

- **App Path**: `payload/platform/infisical`
- **Namespace**: `infisical`
- **External Link**: [https://secrets.infra.k8s.wlkr.ch](https://secrets.infra.k8s.wlkr.ch)

## Usage

To use Infisical in your workloads, you define an `InfisicalSecret` resource. The Infisical Secrets Operator will then fetch the secrets from the configured project/environment in Infisical and create a native Kubernetes Secret.

### Example: Syncing Secrets

Create an `InfisicalSecret` in your application's namespace:

```yaml
apiVersion: secrets.infisical.com/v1alpha1
kind: InfisicalSecret
metadata:
  name: my-app-secrets
  namespace: my-app
spec:
  # The path in Infisical to fetch secrets from
  secretPath: "/my-app"
  
  # The Kubernetes Secret to create
  managedSecretReference:
    secretName: my-app-secret
    creationPolicy: Owner
    
  # Authentication (assuming Machine Identity is configured)
  authentication:
    universalAuth:
      secretsScope:
        projectSlug: "my-project-slug"
        envSlug: "prod"
      credentialsRef:
        secretName: infisical-auth
        secretNamespace: infisical
```

This will create a Kubernetes Secret named `my-app-secret` containing all secrets found at `/my-app` in the `prod` environment of project `my-project-slug`.

### Authentication

The `InfisicalSecret` requires authentication details. We typically use Universal Auth with Machine Identities. The credentials (Client ID and Client Secret) should be stored in a collection-level Kubernetes Secret (e.g., `infisical-auth`) that the `InfisicalSecret` can reference.
