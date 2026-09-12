---
description: "The CI workflows that lint this repository and the Renovate configuration that keeps dependencies current."
---

# Maintenance

The automation that keeps this repository from rotting: CI workflows that lint
everything, and Renovate, which does the dependency chasing that nobody does
reliably by hand for more than about six weeks.

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

### Container images

- **Boot Server**: `build-boot-server.yaml` builds `boot_server/Dockerfile` and
    pushes it to `ghcr.io/janwelker/homelab/boot-server` on every push to `main`
    that touches `boot_server/**`, tagged `latest` and with the full commit SHA.
    Pull requests build the image and discard it — a fork PR has no registry
    write access, and a branch that could publish the image the boot host pulls
    is not a review step. See [Boot Server](../boot_server/index.md#the-image).

### Vendored assets

- **Fonts**: Pull requests touching `scripts/update-fonts.sh` or
    `docs/assets/fonts/**` re-download both pinned font releases and diff them
    against the committed `woff2` files (`fonts-check.yaml`). It exists because
    Renovate can move those pins but cannot write the binaries -- see
    [Vendored binaries need a follow-up commit](#vendored-binaries-need-a-follow-up-commit).

## Dependency Management

We use **Renovate** to automate dependency updates. The configuration is
located in `renovate.json`.

### Policy

- **Schedule**: Renovate runs at any time, with no PR hourly or concurrency
    limits.
- **Grouping**: None. Every component gets its own pull request, so a failing
    update never holds up an unrelated one and the branch name says what moved.
- **Automerge**: One policy for everything — patch and minor automerge, majors
    wait for a human. There are no per-area exceptions: a Flatcar or Kubernetes
    minor lands the same way a Grafana chart patch does. What that buys is a
    config short enough to hold in your head; what it costs is listed under
    [What automerging everything actually means](#what-automerging-everything-actually-means).
- **Pinning**: The `config:best-practices` preset is enabled, so GitHub Actions
    are pinned to commit SHAs and container images to digests.
- **Scope**: Renovate checks Python dependencies (`pyproject.toml`, `uv.lock`,
    `boot_server/requirements.txt`), Docker images — including the base image in
    `boot_server/Dockerfile` — GitHub Actions, Kubernetes manifests, ArgoCD
    resources, Helm values (`payload/**/values.yaml`), and pre-commit hooks. Custom regex
    managers track the Flatcar, Kubernetes, containerd, kube-vip and syslinux
    versions pinned in `ansible/inventory.yaml`, and the font releases pinned in
    `scripts/update-fonts.sh`. The `Makefile` is deliberately not in that list —
    see [Bootstrap versions are derived, not pinned](#bootstrap-versions-are-derived-not-pinned).

### What automerging everything actually means

A single policy is worth the loss of the per-area exceptions that used to exist,
but two of them were load-bearing and are worth naming rather than discovering.

**Flatcar, Kubernetes and containerd now land unattended.** A minor bump to
`ansible/inventory.yaml` changes what a *newly provisioned* node installs, not
what a running node runs, so nothing moves under the cluster when the PR merges.
It surfaces the next time a node is rebuilt. Kubernetes is the one to watch:
kubeadm cannot skip a minor, so a cluster left unrebuilt across two automerged
Kubernetes minors has an upgrade path that no longer exists. See
[Upgrades](../operations/upgrades.md).

**Font bumps automerge without their second commit.** The PR moves the pin in
`scripts/update-fonts.sh` and nothing else; the `woff2` files under
`docs/assets/fonts/` are still the old ones, and nobody is asked before that
merges. The site keeps working -- it serves the fonts it has -- but the pin
claims a release the repository does not contain. The `fonts-check.yaml`
workflow is what stops it: the check fails on exactly that branch, and a red
check holds the automerge. It only holds it while the check is one `main`
requires, so if branch protection is ever rebuilt, put it back.

### Ways a manager can silently do nothing

Each of these went unnoticed for long enough to let dependencies drift, so they
are worth knowing about before adding a new one. They share a failure mode, and
it is the worst one automation has: the config looks right, the tool reports
success, and nothing is actually being checked. A silent no-op is much harder to
notice than an error.

The quickest way to prove a manager does what it claims is to run the extraction
locally and read what comes back:

```bash
LOG_LEVEL=debug npx --yes renovate@latest --platform=local --dry-run=lookup
```

It needs no credentials for a public repository, changes nothing, and prints
every dependency it found along with the reason it skipped any of them. Both of
the version-level cases below were found that way, after the Dependency
Dashboard showed a file with no dependencies under it.

**The `pre-commit` manager ships disabled.** Renovate's own default for it is
`enabled: false`, which is very easy to miss because a `packageRules` entry
matching `matchManagers: ["pre-commit"]` looks for all the world like it is doing
something. It is not, unless `renovate.json` also sets:

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
with `valueFiles`, the way Cilium, ArgoCD and cert-manager already do. Either
works; the annotation is cheaper for a single tag, and the values file pays off
the moment there is a second one -- or the moment `make install-core` needs the
same settings, since a bootstrap target can pass a file to Helm and cannot pass
a `valuesObject`.

### One pin deliberately exists twice

`tftpy` is pinned in both `pyproject.toml` and `boot_server/requirements.txt`,
which is exactly what
[Bootstrap versions are derived, not pinned](#bootstrap-versions-are-derived-not-pinned)
argues against — and is still the right call here. The image installs from
`requirements.txt` because the runtime half of `uv.lock` is almost entirely
Ansible, which never runs in the container; the copy in `pyproject.toml` is what
lets `pylint` resolve the import in CI.

Both are default-manager files, so Renovate moves both, in two pull requests that
land minutes apart. The failure mode if one lags is visible rather than silent:
`pylint` resolves a different version than the image installs, and the image is
the one that serves.

### A dependency can be extracted and still never looked up

`syslinux_version` in `ansible/inventory.yaml` is a two-part version, and
Renovate's default `semver-coerced` refuses it:

```text
DEBUG: Dependency syslinux has unsupported/unversioned value 6.03 (versioning=semver-coerced)
DEBUG: Skipping syslinux because no currentDigest or pinDigests
```

The custom manager matched the file and extracted the value; the lookup was then
dropped, so no update was ever raised. The fix is a `versioningTemplate` on that
manager -- `regex:^(?<major>\d+)\.(?<minor>\d+)$`, the same shape the Inter
pin uses for the same reason. 6.03 is still upstream's newest stable, so nothing
had actually drifted, which is precisely why it went unnoticed.

The neighbouring trap is a regex that cannot match at all. The diff preview
manager looked for `dag-andersen/argocd-diff-preview` -- the GitHub org -- while
the image on Docker Hub is `dagandersen/argocd-diff-preview`, without the
hyphen, which is what the workflow correctly uses. Nothing matched, so the image
sat on v0.2.2 while upstream reached v0.2.14, and being invisible it was the one
container image in the repository that `config:best-practices` never pinned to a
digest.

### Bootstrap versions are derived, not pinned

`make install-core` and `make install-argo` install Cilium, cert-manager, the
Gateway API and Prometheus operator CRDs, and ArgoCD itself before ArgoCD exists
to manage them. The `Makefile` used to carry its own pins for those, and
Renovate never saw them — by the time anybody looked, the bootstrap Cilium was
two minors behind the one the cluster was actually running.

The fix was not another custom manager. A second copy of a version is the
problem; tracking both copies only makes the drift arrive in pairs. Each target
now reads `targetRevision` out of the `Application` manifest that owns the
component after the handover:

```make
CILIUM_VERSION := $(call chart_version,payload/platform/cilium/application.yaml)
```

So there is nothing `Makefile`-shaped in `renovate.json`, and nothing to add
there when a new bootstrap component appears — point the target at the manifest
instead. The targets abort with an explicit error if a manifest moves and the
lookup comes back empty, because the alternative is Helm receiving an empty
`--version` and cheerfully installing whatever is latest.

### Vendored binaries need a follow-up commit

The docs site self-hosts its webfonts, so `docs/assets/fonts/` holds `woff2`
files that no manager can rewrite. `scripts/update-fonts.sh` pins the two
upstream releases those files come from, and a custom manager tracks the pins:

```bash
# renovate: datasource=github-releases depName=inter packageName=rsms/inter versioning=regex:^v(?<major>\d+)\.(?<minor>\d+)$
INTER_VERSION="v4.1"
```

A Renovate PR therefore changes one line and nothing else — the fonts it claims
to update are still the old ones, which is a uniquely deceptive kind of green
tick. Check out the branch, run `make fonts` to
fetch the release the pin now names, and commit the result before merging.
`make fonts-check` re-downloads both releases and diffs them against what is
committed, so it will tell you whether a branch still needs that second commit.

Nobody is prompted to do any of that under the flat automerge policy, so
`fonts-check.yaml` runs it on every PR that touches the pins or the files and
fails the branch until the second commit arrives -- see
[What automerging everything actually means](#what-automerging-everything-actually-means).

The `versioning` in the annotation is a `regex:` rather than `semver`: Inter
tags prereleases as `v4.0-beta9h`, and only accepting two-part tags keeps those
out of the update stream.
