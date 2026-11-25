---
description: "The CI workflows that lint this repository and the Renovate configuration that keeps dependencies current."
---

# Maintenance

GitHub Actions workflows lint everything; Renovate chases dependencies.

## CI workflows

All in `.github/workflows/`.

| Workflow | Trigger | What it checks |
| --- | --- | --- |
| `lint-ansible.yaml` | `ansible/**` | `ansible-lint` |
| `ignition-render.yaml` | `ansible/**` | `make ignition-check`: `butane --strict`, `ignition-validate` and `kubeadm config validate` on every host's configs |
| `lint-python.yaml` | `boot_server/*.py`, `scripts/*.py`, `tests/*.py`, `pyproject.toml`, `uv.lock` | `ruff check`, `ruff format --check`, `pytest` |
| `lint-yaml.yaml` | `**/*.yaml` | `yamllint` |
| `lint-markdown.yaml` | `**/*.md` | `markdownlint-cli2` |
| `docs.yaml` | push to `main` under `docs/**`, `overrides/**`, `zensical.toml`, `pyproject.toml`, `uv.lock` | Builds and publishes the site — [Documentation](contributing.md#documentation). Only the deploy job, which runs no dependency code, can push |
| `preview.yaml` | pull requests on the same paths | Publishes a preview under `pr-preview/`, same split as `docs.yaml` |
| `argo-diff-preview.yaml` | pull requests under `payload/**` | Comments the rendered manifest diff against `main`. It mounts the Docker socket for kind; the token is read-only and the runner ephemeral |
| `server-dry-run.yaml` | pull requests under `payload/**` | Applies the rendered payload `--dry-run=server` to a kind cluster with our CRDs and admission policies; `tests/test_conventions.py` checks the documented rules |
| `image-scan.yaml` | pull requests under `payload/**` | Trivy on the images the PR changes; fails on a fixable CRITICAL the replaced image did not carry, so a finding [waiting on a release](../operations/vulnerabilities.md#filing-policy) does not block the bump towards it |
| `lint-shell-actions.yaml` | `**/*.sh`, `.github/workflows/**` | `shellcheck`, `actionlint`, `zizmor` |
| `docs-length.yaml` | pull requests under `docs/**` | No existing page grows, dated logs aside; a new component takes the `docs-growth` label |
| `renovate-validate.yaml` | `renovate.json` | `renovate-config-validator` |
| `renovate-coverage.yaml` | every path that can hold a pin | Fails on a pin no Renovate manager extracts |
| `secret-scan.yaml` | every pull request | `gitleaks` over the PR's commits, the same version as the pre-commit hook; a manual run scans the whole history |
| `ci-ok.yaml` | every pull request | Waits for every other check and fails if one failed; the only required check, so the others keep their path filters |
| `fonts-check.yaml` | `scripts/update-{fonts,mermaid}.sh`, `docs/assets/{fonts,javascripts}/**` | The committed files match the pins — [Vendored assets](contributing.md#vendored-assets) |

## Renovate

Configured in `renovate.json`, on top of the shared preset
[JanWelker/renovate-config](https://github.com/JanWelker/renovate-config),
which holds the policy for every repository. Every component gets its own pull
request so a failing update never holds up an unrelated one; the only groups
are the sets below that must move together.

### Automerge policy

- **The preset's rules:** three days between a release and its PR, patch and
    minor automerge once `ci-ok` is green, majors open at 19:00 and wait for a
    human, and security fixes skip both the wait and the schedule. The
    preset's README says why for each.
- **Cluster-wide charts and workflow actions wait for a human.** Argo CD
    applies a merge within a minute and these charts hold cluster-wide
    rights, so no update of them automerges; the list is in `renovate.json`.
    Cilium's pending patch and each minor get separate PRs, merged in that
    order, the only
    [path upstream tests](https://docs.cilium.io/en/stable/operations/upgrade/).
- **A Kubernetes minor waits for a human.** `kubernetes_version` is walked
    forward by hand in version-skew order; an automerged minor would
    provision a kubelet ahead of the API server — see [Upgrades](../operations/upgrades.md#upgrading-a-minor-version-deliberately).
- **Pairs that must move together are grouped into one PR:**
    `prometheus-operator-crds` with `kube-prometheus-stack`, because the CRDs
    must not lag the operator; `rook-ceph` with `rook-ceph-cluster`, because
    Rook requires both charts on the same version; and `markdownlint-cli2` in
    the workflow with its pre-commit hook, because the two must lint alike;
    `gitleaks` likewise; the `external-snapshotter` tag with the
    `snapshot-controller` image it pins, because upstream's manifest at a tag
    names the previous release's image (a third-party image, so the pair waits for a human); the
    two ARC charts, because the controller deletes a scale set whose chart
    version differs from its own; the OpenBao chart with the image the
    snapshot CronJob runs its CLI from, so the CLI never lags the server it
    snapshots; and every Python pin (`requires-python`,
    `.python-version`, the `python` images), because `uv sync` fails when
    they name different minors.
- **Python ranges bump.** `pyproject.toml` declares `>=` ranges, which a new
    release already satisfies; `rangeStrategy: bump` is what makes Renovate
    open a PR for the linters and the docs generator at all.
- **Flatcar and Kubernetes bumps change what a newly provisioned
    node installs, not what a running node runs** — see
    [Upgrades](../operations/upgrades.md).
- **A vendored-asset bump cannot automerge without its second commit:**
    `fonts-check.yaml` fails on it and `ci-ok` waits for it — see
    [Vendored assets](contributing.md#vendored-assets).
- `config:best-practices` pins GitHub Actions to commit SHAs and container
    images to digests, and collects every pin into one shared
    `renovate/pin-dependencies` branch.

### Scope

Python (`pyproject.toml`, `uv.lock`), Docker images, GitHub Actions, Kubernetes
and ArgoCD manifests, Helm values files (`payload/**/values.yaml`) and
pre-commit hooks, the CI npm tools (`.github/tools/package-lock.json`), plus
custom regex managers for the versions in `ansible/inventory.yaml`, the pins in
`scripts/update-{fonts,mermaid}.sh`, any `# renovate:` annotation, every
CloudNativePG `imageName` under `payload/`, the `selinuxd` image digest, and the
tools workflows install by name (the diff-preview image, Trivy, kind, Helm). The `Makefile` is not
tracked; it reads `targetRevision` from the owning `application.yaml`
([Version pins](../architecture/gitops.md#version-pins)). The Kubernetes
sysext and the Gateway API CRDs carry a sha256 beside the pin, checked before
use and bumped with the version by the `github-release-attachments` datasource.

### Manager rules

A misconfigured manager fails silently: the run succeeds and nothing is
checked. `renovate-coverage.yaml` catches that; locally, after
`npm ci --prefix .github/tools`:

```bash
uv run --no-project scripts/renovate-coverage.py
```

The rules, each in `renovate.json`:

- **`pre-commit` must be enabled explicitly** (`"pre-commit": {"enabled": true}`);
    Renovate ships that manager disabled, and a `packageRules` entry for it does
    nothing on its own.
- **The `helm-values` manager reads only real values files.** An image tag
    inline in an Application's `helm.valuesObject:` is invisible to it; put a
    `# renovate: datasource=... depName=...` comment on the line above so the
    annotation manager picks it up, or move the values into a `values.yaml`.
- **A custom manager that pins a full image reference needs
    `autoReplaceStringTemplate`.** Without it the pin cannot be written, and one
    unwritable pin fails the whole shared pin branch (`Error updating branch:
    update failure` on the Dependency Dashboard, the only symptom, since
    version bumps keep working).
- **A bare Helm `tag:` value goes in the `pinDigests: false` rule.** A tag like
    `3.4.4-alpine` has no position for a digest; leave one out and the same
    banner comes back.
- **A two-part version needs a `versioningTemplate`.** The default
    `semver-coerced` rejects `6.03`, so the dependency is extracted and then
    dropped before lookup; `syslinux` and the Inter font pin carry a `regex:`
    versioning.
- **Match the registry's spelling, not the GitHub org's.** The diff-preview
    image is `dagandersen/argocd-diff-preview` on Docker Hub; a regex on
    `dag-andersen` matches nothing and reports nothing.
- **Hold a major with a regex, not a range.** `"allowedVersions": "/^17\\./"`
    is matched before version parsing; a range like `<18` is graded by npm
    semver, where a three-part tag such as `17.11.1-trixie` parses as a
    prerelease and is dropped from every range that does not name one.
    Python is held at 3.14 this way until ansible-core supports 3.15.
- **Check that the currently pinned version passes its own rule.** A filter
    that excludes what is deployed excludes everything, silently.

## Language statistics

GitHub's language bar comes from
[Linguist](https://github.com/github-linguist/linguist), which counts bytes of
files it classifies as a *language*. YAML and Markdown are excluded by default,
so `.gitattributes` marks YAML detectable and the three vendored Grafana
dashboards as vendored: their JSON would otherwise be most of the bar. A
vendored dashboard added later needs its own line there.
