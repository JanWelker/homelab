---
description: "Repository setup, the checks to run before a pull request, commit conventions, and how the documentation site is built and published."
---

# Contributing

A merge to `main` is a deployment: ArgoCD applies `payload/` to real hardware,
and there is no staging cluster in between. The checks below exist to make that
routine.

## Setup

```bash
uv sync                     # virtualenv and dependencies, including dev tools
uv run pre-commit install   # the same linters CI runs
```

## Checks

```bash
uv run pre-commit run --all-files       # ansible-lint, markdownlint, pylint, yamllint, docs build
uv run zensical build --clean --strict  # docs alone; strict fails on broken links and orphan pages

# Before touching payload/: valid objects, and what Helm will actually render
kubectl apply --dry-run=client -f payload/platform/<component>/
helm template <name> <repo>/<chart> --version <targetRevision> -f <values>
```

`--strict` is what CI uses. A page not registered in `nav` in `zensical.toml`
fails the build, deliberately. On a pull request, the `argo-diff-preview`
workflow comments the rendered ArgoCD manifest diff between `main` and your
branch; read it, since a three-line values change can render as two hundred
lines of different objects. The full list of workflows is in
[Maintenance](maintenance.md#ci-workflows).

## Branches and commits

Branch names are prefixed by area: `docs/`, `feat/`, `fix/`, `chore/`. Commit
messages follow [Conventional Commits](https://www.conventionalcommits.org/):

```text
docs: explain the stack choices and collect the known limitations
fix(bootstrap): repoint the install targets at the files that exist
chore(deps): update helm release kube-prometheus-stack to v89
```

The body explains *why*; the diff already says what.

!!! warning "Do not hand-edit version numbers"
    `targetRevision` in the manifests and the versions in `ansible/inventory.yaml` are owned by Renovate — see [Maintenance](maintenance.md#renovate). A hand bump conflicts with, or is reverted by, the next Renovate PR. The bootstrap targets in the `Makefile` read the same `targetRevision` values rather than pinning their own — see [Version pins](../architecture/gitops.md#version-pins).

## Review

`.github/CODEOWNERS` assigns every path to the repository owner, so every pull
request needs that review. Renovate PRs for patch and minor updates automerge;
majors wait for a human.

`.agent/rules/general-rules.md` holds the standing rules for AI coding agents
working here: GitOps only, docs in `docs/`, no hand-edited versions. Keep it in
sync when those conventions change.

## Documentation

The site is built with [Zensical](https://zensical.org/) from `docs/`,
configured in `zensical.toml`, and published to GitHub Pages by
`.github/workflows/docs.yaml` at <https://homelab.wlkr.ch/>. It is
deliberately not hosted on the cluster, so it stays readable when the cluster
is not.

| Stage | What happens |
| --- | --- |
| Build | On push to `main` touching `docs/**`, `overrides/**`, `zensical.toml`, `pyproject.toml` or `uv.lock`, `docs.yaml` runs `zensical build --clean --strict` |
| Deploy | The workflow drops a `.nojekyll` marker into `site/` (otherwise Pages runs Jekyll, which drops paths it considers private) and pushes to `gh-pages`, leaving `pr-preview/` untouched |
| Previews | `preview.yaml` builds every pull request matching the same paths, forks included, and publishes it under `pr-preview/` on `gh-pages`; closing the PR removes it. Its path filter must match `docs.yaml`, because this is the only `--strict` build a PR gets. Only the publish step is skipped for fork PRs: they have no write access |

Locally:

```bash
uv run zensical serve   # http://localhost:8000, rebuilds on save
uv run zensical build   # one-off build into site/ (gitignored)
```

### Conventions

- Every page is registered in `nav`; cross-references are relative Markdown
    paths (`platform/openbao.md`, `../quickstart.md`) so they resolve on the
    site and on GitHub.
- Diagrams use Mermaid fences; Zensical loads the runtime on pages that contain
    one.
- Markdown is linted by `lint-markdown.yaml` and the `markdownlint-cli2`
    pre-commit hook. `MD046`: a blank line followed by an indented line reads as
    a code block, so keep `!!!` admonition bodies to a single paragraph.
    `MD007`: nested lists indent by four spaces.
- Inline HTML is limited to `<div>` (`MD033` in `.markdownlint-cli2.yaml`),
    which the card grids on hub pages need.
- Theme templates are overridden by a same-named file under `overrides/`
    (`theme.custom_dir`). Currently only `partials/source.html`, which drops the
    repository-facts call that 404s because this repository publishes no
    releases.

### Fonts

Fonts are self-hosted so the site makes no third-party requests:
`theme.font = false` in `zensical.toml`, `docs/stylesheets/fonts.css` declares
Inter and JetBrains Mono from the `woff2` files in `docs/assets/fonts/`, and
each SIL OFL licence sits next to them. `scripts/update-fonts.sh` pins both
upstream releases ([Inter](https://github.com/rsms/inter/releases),
[JetBrains Mono](https://github.com/JetBrains/JetBrainsMono/releases)) and
Renovate tracks the pins.

!!! warning "A font-pin PR needs a second commit"
    Renovate can move the pin but cannot write the binaries, so its PR changes one line and the `woff2` files are still the old release. Check out the branch, run `make fonts` to download what the pin now names, and commit the result. `make fonts-check` (run by `fonts-check.yaml` on every such PR) diffs the downloads against what is committed and fails the branch until that commit arrives, which is what holds the automerge.
