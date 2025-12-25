# Maintenance

This section outlines the automated systems used to maintain the repository, including Continuous Integration/Continuous Deployment (CI/CD) workflows and dependency management.

## CI/CD Workflows

The project uses GitHub Actions for automation, defined in `.github/workflows`.

### Linting

To ensure code quality and consistency, several linting workflows are configured:

- **Ansible**: Checks Ansible playbooks for best practices and errors (`lint-ansible.yaml`).
- **Python**: Lints Python scripts (e.g., in `boot_server`) using standard Python linters (`lint-python.yaml`).
- **YAML**: Validates all YAML files in the repository to prevent syntax errors (`lint-yaml.yaml`).
- **Markdown**: Checks documentation files for formatting issues (`lint-markdown.yaml`).

### Documentation Build

- **Docs Build & Publish**: The `docs.yaml` workflow automatically builds this MkDocs site and publishes it as a Docker image to the GitHub Container Registry (GHCR) whenever changes are pushed to the `main` branch. It then commits the new image tag to the `deployment.yaml` file, triggering a GitOps sync via ArgoCD.

## Dependency Management

We use **Renovate** to automate dependency updates. The configuration is located in `renovate.json`.

### Policy

- **Schedule**: Renovate runs **every weekend** to minimize noise during the week.
- **Grouping**:
  - **Non-Major Updates**: Minor and patch updates, as well as digest updates, are grouped into a single "Non-Major Updates" Pull Request. This reduces the number of PRs to review.
  - **Major Updates**: Major version updates are raised as separate Pull Requests to ensure they are reviewed carefully for breaking changes.
- **Pinning**: We generally use the `pin` strategy to lock dependencies to specific versions for stability.
- **Scope**: Renovate checks Docker images, GitHub Actions, Kubernetes manifests, ArgoCD resources, and Helm values (specifically in `payload/*/values.yaml`).
