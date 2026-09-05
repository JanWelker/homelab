---
description: "Setting up the repository for development, the checks that run, and what to do before opening a pull request."
---

# Contributing

## One-time setup

```bash
uv sync                 # virtualenv and dependencies, including dev tools
uv run pre-commit install
```

Installing the hooks matters: the same linters run in CI, and every one of them
is faster to satisfy locally than in a PR.

## Running the checks

```bash
uv run pre-commit run --all-files       # ansible-lint, markdownlint, pylint, yamllint
uv run zensical build --clean --strict  # docs; strict fails on broken links
```

`--strict` is what CI uses, so a local build that passes is a docs build that
passes. A page that is not registered in `nav` in `zensical.toml` fails here.

## Branches and commits

Branch names are prefixed by area: `docs/`, `feat/`, `fix/`, `chore/`.

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/),
matching the existing history:

```text
docs: explain the stack choices and collect the known limitations
fix(bootstrap): repoint the install targets at the files that exist
chore(deps): update helm release kube-prometheus-stack to v89
```

Write the body to explain *why*, not what — the diff already says what.

## Changing manifests under `payload/`

Everything under `payload/` is applied by ArgoCD, so a mistake there reaches the
cluster on merge. Two checks before pushing:

```bash
# Valid YAML and valid Kubernetes objects, without touching the cluster
kubectl apply --dry-run=client -f payload/platform/<component>/

# What a Helm-sourced Application will actually render
helm template <name> <repo>/<chart> --version <targetRevision> -f <values>
```

Opening the PR adds a third: the `argo-diff-preview` workflow comments the
rendered ArgoCD manifest diff between `main` and your branch, which is the most
reliable review artifact for a `payload/` change.

!!! warning
    Do not hand-edit version numbers. `targetRevision` in the manifests and the
    versions in `ansible/inventory.yaml` are owned by Renovate — see
    [Maintenance](maintenance.md). The one exception is the `Makefile`, which
    pins the components installed before ArgoCD exists and which Renovate does
    not track.

## Changing documentation

See [Documentation System](documentation.md) for the build, the conventions, and
the two markdownlint rules that most often bite (`MD046` on multi-paragraph
admonitions, `MD007` on nested list indentation).

## Review

`.github/CODEOWNERS` assigns every path to the repository owner, so all pull
requests need that review before merging. Renovate PRs for patch and minor
updates automerge within their group; majors and anything in
`Core Infrastructure` always wait for a human.

## Agent-assisted changes

`.agent/rules/general-rules.md` holds the standing rules for AI coding agents
working in this repository — GitOps only, docs in `docs/`, and no hand-edited
version numbers. Keep it in sync when those conventions change.
