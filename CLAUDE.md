# Flatcar homelab

Bare-metal Kubernetes on Flatcar Container Linux. Ansible and a PXE boot server
bring the nodes up; ArgoCD owns everything after that.

## Deployment model

`payload/` is the cluster and ArgoCD syncs all of it; nothing is applied by
hand after bootstrap. Read `docs/architecture/gitops.md` before touching
`payload/`. The rules there that cost the most when skipped:

- Nothing orders the Applications and every `application.yaml` carries the
  same `syncPolicy` with `retry`; copy it from a sibling, except `kube-vip`
  ([Sync policy](docs/architecture/gitops.md#sync-policy)).
- A fresh cluster looks stuck at OpenBao until the `make bao-*` targets run
  ([Bootstrap convergence](docs/architecture/gitops.md#bootstrap-convergence)).
- Moving a resource between Applications takes two merges, and deleting an
  Application by hand means stripping its finalizer first
  ([Moving a resource](docs/architecture/gitops.md#moving-a-resource-to-another-application)).
- Each version is pinned once, in the owning `application.yaml`; the
  `Makefile` reads it from there
  ([Version pins](docs/architecture/gitops.md#version-pins)). Renovate's
  grouping is in `docs/development/maintenance.md`.
- Long explanations belong in `docs/`, not in YAML comments. Changes land
  through PRs off `main`.

## Reading the docs

`docs/` is the reference for this repository; read it before asking. The
applications repository's docs are one fetch away at
<https://homelab-apps.wlkr.ch/llms.txt> (page index) or
<https://homelab-apps.wlkr.ch/llms-full.txt> (every page). This site publishes
the same two at <https://homelab.wlkr.ch/llms.txt> and
<https://homelab.wlkr.ch/llms-full.txt>.

## Writing docs

`docs/` is reference material, not an essay collection. It was condensed from
57k to 37k words once; keep it there.

- **Say each fact once, site-wide.** The sync policy, bootstrap convergence
  and version pins live in `docs/architecture/gitops.md`; the rollout order and the
  2.5x-peak memory rule in `docs/platform/index.md`; the OpenBao unseal
  consequences in `docs/platform/openbao.md`; the inventory variables in the
  Quickstart; the Renovate policy in `docs/development/maintenance.md`.
  Everywhere else is one clause and a link.
- **One sentence of why per rule.** No history, postmortems, or "this page
  previously claimed". If the reasoning needs a paragraph it goes in
  `docs/architecture/decisions.md`.
- **Never restate values from `payload/` or `ansible/`.** Link the file and
  explain why the setting is what it is. Numbers rot on the next Renovate merge.
- **Tables and numbered steps over prose.** A runbook is numbered steps with one
  code block each. Headings are nouns a reader would search for, not essay
  titles.
- **Platform pages share one skeleton:** intro, At a glance, Configuration,
  Usage, Health check, Pitfalls, and Recovery only where a real runbook exists.
- **Dated logs are their own page**, like `docs/operations/triage-2026-09-20.md`.
  The reference page keeps the method and links the log.
- The build validates links and anchors. Grep for `#anchor` before renaming a
  heading.

## Bootstrap

`make bootstrap` installs only what ArgoCD needs to run; the steps, what each
one waits for and where `output/credentials/` comes from are in
`docs/quickstart.md`. `make clean` deletes the credentials and they cannot be
regenerated identically.

## Checks before pushing

```bash
uv run yamllint -f github .
uv run zensical build --clean --strict        # docs site
markdownlint-cli2 '**/*.md' '!**/.venv' '!.agent'
uv run ruff check . && uv run ruff format --check .   # python only
uv run pytest -q                              # python only
cd ansible && uv run ansible-lint             # ansible only
```
