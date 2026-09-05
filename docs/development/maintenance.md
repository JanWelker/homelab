---
description: "The CI workflows that lint this repository and the Renovate configuration that keeps dependencies current."
---

# Maintenance

This section outlines the automated systems used to maintain the repository,
including Continuous Integration/Continuous Deployment (CI/CD) workflows and
dependency management.

## CI/CD Workflows

The project uses GitHub Actions for automation, defined in `.github/workflows`.

### Linting

To ensure code quality and consistency, several linting workflows are configured:

- **Ansible**: Checks Ansible playbooks for best practices and errors
    (`lint-ansible.yaml`).
- **Python**: Lints Python scripts (e.g., in `boot_server`) using standard
    Python linters (`lint-python.yaml`).
- **YAML**: Validates all YAML files in the repository to prevent syntax
    errors (`lint-yaml.yaml`).
- **Markdown**: Checks documentation files for formatting issues
    (`lint-markdown.yaml`).

### Documentation Build

- **Docs Build & Publish**: The `docs.yaml` workflow builds this
    [Zensical](https://zensical.org/) site and deploys it to GitHub Pages
    whenever changes land on `main` under `docs/**`, `overrides/**`, or
    `zensical.toml`. See
    [Documentation System](documentation.md) for the full pipeline.

### GitOps

- **Argo Diff Preview**: Pull requests touching `payload/**` get an automated
    comment showing the rendered ArgoCD manifest diff between `main` and the
    branch (`argo-diff-preview.yaml`).
- **Renovate Config**: Changes to `renovate.json` are validated with
    `renovate-config-validator` (`renovate-validate.yaml`).

## Dependency Management

We use **Renovate** to automate dependency updates. The configuration is
located in `renovate.json`.

### Policy

- **Schedule**: Renovate runs at any time, with no PR hourly or concurrency
    limits.
- **Grouping**: Updates are grouped by area rather than by update type —
    `Platform Infrastructure` (`payload/platform/**`), `Workloads`
    (`payload/workloads/**`), `Dev Tooling` (Python tooling and pre-commit),
    `DevOps` (GitHub Actions, Ansible, pre-commit hooks), and
    `Core Infrastructure` (Flatcar, Kubernetes, containerd, syslinux).
- **Automerge**: Patch and minor updates automerge within their group. Major
    updates and everything in `Core Infrastructure` always require review.
- **Pinning**: The `config:best-practices` preset is enabled, so GitHub Actions
    are pinned to commit SHAs and container images to digests.
- **Scope**: Renovate checks Python dependencies (`pyproject.toml`, `uv.lock`),
    Docker images, GitHub Actions, Kubernetes manifests, ArgoCD resources,
    Helm values (`payload/**/values.yaml`), and pre-commit hooks. Custom regex
    managers track the Flatcar, Kubernetes, containerd, kube-vip and syslinux
    versions pinned in `ansible/inventory.yaml`.

### Two things Renovate cannot see by default

Both of these went unnoticed for long enough to let dependencies drift, so they
are worth knowing about before adding a new one.

**The `pre-commit` manager ships disabled.** Renovate's own default for it is
`enabled: false`, which is easy to miss because a `packageRules` entry matching
`matchManagers: ["pre-commit"]` looks like it is doing something. It is not,
unless `renovate.json` also sets:

```json
"pre-commit": { "enabled": true }
```

Without that line `.pre-commit-config.yaml` is never updated, and the hook
versions there silently diverge from the equivalent pins in `pyproject.toml`.

**The `helm-values` manager only reads real values files.** Its
`managerFilePatterns` is scoped to `payload/**/values.yaml`, and that is not a
configuration choice that can be widened — the manager parses a values document,
so an image tag written inline in an ArgoCD `Application` under
`helm.valuesObject:` is invisible to it. Those tags are the reason the generic
annotation manager exists: put a `# renovate:` comment on the line above and the
custom regex manager picks it up.

```yaml
image:
  repository: quay.io/openbao/openbao
  # renovate: datasource=github-releases depName=openbao/openbao extractVersion=^v(?<version>.+)$
  tag: 2.6.2
```

The alternative is to move the values into a real `values.yaml` and reference it
with `valueFiles`, the way Cilium and ArgoCD already do. Either works; the
annotation is cheaper for a single tag.
