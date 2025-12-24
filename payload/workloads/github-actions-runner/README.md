# GitHub Actions Runner Controller (ARC)

Self-hosted GitHub Actions runners deployed using the [Actions Runner Controller](https://github.com/actions/actions-runner-controller).

## Components

- **arc-systems**: The controller that manages runner lifecycle
- **arc-runner-set**: The runner scale set that spawns runner pods

## Architecture

The ARC uses the newer `gha-runner-scale-set` architecture (not the legacy `actions-runner-controller`):

```text
GitHub Actions Job → AutoscalingListener (polls Actions API) → EphemeralRunner Pod
```

## Authentication

Uses **GitHub App** authentication. The secret `pre-defined-secret` in the
`arc-systems` namespace contains:

- `github_app_id`: The GitHub App ID
- `github_app_installation_id`: The installation ID for your repository/organization
- `github_app_private_key`: The PEM-encoded private key

### Required GitHub App Permissions

- **Repository Permissions**:
  - `Administration`: Read and write
  - `Actions`: Read and write
  - `Metadata`: Read-only (default)

## Configuration

### Helm Values (via ArgoCD Application)

Key settings in `application.yaml`:

```yaml
githubConfigUrl: "https://github.com/YOUR_ORG/YOUR_REPO"
githubConfigSecret: "pre-defined-secret"
minRunners: 1  # Minimum idle runners
```

### RBAC

The `extra-rbac.yaml` file provides additional permissions for the controller to
create listener Roles and RoleBindings dynamically.

## Usage

In your workflow files, reference the runner:

```yaml
jobs:
  build:
    runs-on: arc-runner-set
    steps:
      - run: echo "Hello from self-hosted runner!"
```

## ArgoCD Notes

The controller creates several resources dynamically that are excluded from
ArgoCD tracking via `resource.exclusions` in `values.yaml`:

- `AutoscalingListener`
- `EphemeralRunnerSet`
- `EphemeralRunner`

The Application also uses `ignoreDifferences` for:

- CRD webhook fields
- Dynamic annotations on `AutoscalingRunnerSet`
