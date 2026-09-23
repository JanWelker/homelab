---
description: "The CI workflows that lint this repository and the Renovate configuration that keeps dependencies current."
---

# Maintenance

Two things keep the repository from rotting: GitHub Actions workflows that lint
everything, and Renovate, which chases dependencies.

## CI workflows

All in `.github/workflows/`.

| Workflow | Trigger | What it checks |
| --- | --- | --- |
| `lint-ansible.yaml` | `ansible/**` | `ansible-lint` |
| `lint-python.yaml` | `boot_server/*.py`, `pyproject.toml`, `uv.lock` | `pylint` on the boot server |
| `lint-yaml.yaml` | `**/*.yaml` | `yamllint` |
| `lint-markdown.yaml` | `**/*.md` | `markdownlint-cli2` |
| `docs.yaml` | push to `main` under `docs/**`, `overrides/**`, `zensical.toml`, `pyproject.toml`, `uv.lock` | Builds the site with `--strict` and publishes it — see [Contributing](contributing.md#documentation) |
| `preview.yaml` | pull requests on the same paths | Publishes a preview under `pr-preview/`; fork PRs skipped |
| `argo-diff-preview.yaml` | pull requests under `payload/**` | Comments the rendered ArgoCD manifest diff against `main`; fork PRs skipped |
| `image-scan.yaml` | pull requests under `payload/**` | `scripts/image-scan-diff.py`: renders every Application on `main` and on the PR, scans with Trivy only the images that changed, and fails on a fixable CRITICAL the replaced image did not carry. A finding already [waiting on a release](../operations/vulnerabilities.md#filing-policy) does not block the bump that gets closer to it |
| `renovate-validate.yaml` | `renovate.json` | `renovate-config-validator` |
| `fonts-check.yaml` | `scripts/update-fonts.sh`, `docs/assets/fonts/**` | `make fonts-check`: the committed `woff2` files match the pinned releases — see [Fonts](contributing.md#fonts) |

## Renovate

Configured in `renovate.json`. It runs at any time with no hourly or
concurrency limit, and every component gets its own pull request so a failing
update never holds up an unrelated one. The only groups are the pairs below
that must move together.

### Automerge policy

- **Patch and minor updates automerge; majors wait for a human.** One rule for
    everything, with no per-area exceptions: a Flatcar or Kubernetes minor lands
    the same way a Grafana chart patch does.
- **Three days between a release and its PR** for every datasource that pins
    something a node installs or the cluster runs (`minimumReleaseAge`): long
    enough for an upstream to pull a broken or compromised tag, short enough
    that a fix arrives the same week. Renovate lifts it for its own
    vulnerability-alert PRs.
- **Pairs that must move together are grouped into one PR:**
    `prometheus-operator-crds` with `kube-prometheus-stack`, because the CRDs
    must not lag the operator; `rook-ceph` with `rook-ceph-cluster`, because
    Rook requires both charts on the same version; and `markdownlint-cli2` in
    the workflow with its pre-commit hook, because the two must lint alike.
- **Python ranges bump.** `pyproject.toml` declares `>=` ranges, which a new
    release already satisfies; `rangeStrategy: bump` is what makes Renovate
    open a PR for the linters and the docs generator at all.
- **Flatcar, Kubernetes and containerd bumps change what a newly provisioned
    node installs, not what a running node runs.** kubeadm cannot skip a minor,
    so a cluster left unrebuilt across two automerged Kubernetes minors has to be
    walked forward one at a time — see [Upgrades](../operations/upgrades.md).
- **Font bumps automerge without their second commit** unless
    `fonts-check.yaml` is a required check on `main`. If branch protection is
    ever rebuilt, put it back — see [Fonts](contributing.md#fonts).
- `config:best-practices` pins GitHub Actions to commit SHAs and container
    images to digests, and collects every pin into one shared
    `renovate/pin-dependencies` branch.

### Scope

Python (`pyproject.toml`, `uv.lock`), Docker images, GitHub Actions, Kubernetes
and ArgoCD manifests, Helm values files (`payload/**/values.yaml`) and
pre-commit hooks, plus custom regex managers for the versions in
`ansible/inventory.yaml`, the font pins in `scripts/update-fonts.sh`, and any
`# renovate:` annotation under `payload/`. The `Makefile` is deliberately not
tracked: the bootstrap targets read `targetRevision` out of the owning
`application.yaml` — see [Version pins](../architecture/gitops.md#version-pins).

### Manager rules

A misconfigured manager fails silently: the run succeeds and nothing is
checked. Prove a manager works by reading what it extracts:

```bash
LOG_LEVEL=debug npx --yes renovate@latest --platform=local --dry-run=lookup
```

It needs no credentials for a public repository and prints every dependency it
found and why it skipped any. The rules, each of which is written into
`renovate.json`:

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
    version bumps keep working). Capturing `currentDigest` only lets Renovate
    update a digest that is already there.
- **A bare Helm `tag:` value goes in the `pinDigests: false` rule.** A tag like
    `3.4.4-alpine` has no position for a digest; leave one out and the same
    banner comes back.
- **A two-part version needs a `versioningTemplate`.** The default
    `semver-coerced` rejects `6.03`, so the dependency is extracted and then
    dropped before lookup. `syslinux` and the Inter font pin carry a `regex:`
    versioning for this reason; Inter's also keeps `v4.0-beta9h`-style
    prereleases out.
- **Match the registry's spelling, not the GitHub org's.** The diff-preview
    image is `dagandersen/argocd-diff-preview` on Docker Hub; a regex on
    `dag-andersen` matches nothing and reports nothing.
- **Hold a major with a regex, not a range.** `"allowedVersions": "/^17\\./"`
    is matched before version parsing; a range like `<18` is graded by npm
    semver, where a three-part tag such as `17.11.1-trixie` parses as a
    prerelease and is dropped from every range that does not name one.
- **Check that the currently pinned version passes its own rule.** A filter
    that excludes what is deployed excludes everything, silently.

## Language statistics

GitHub's language bar comes from
[Linguist](https://github.com/github-linguist/linguist), which counts bytes of
files it classifies as a *language*. YAML is classified as data and Markdown as
prose, so both are excluded by default and the bar reported this repository as
Shell and Jinja. `.gitattributes` marks YAML detectable, and marks the three
vendored Grafana dashboards as vendored, because their upstream JSON blobs are
more than half the repository's YAML bytes and would otherwise be most of the
bar. A vendored dashboard added later needs its own line there; GitHub
recalculates on the next push to `main`.
