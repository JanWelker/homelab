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
uv run pre-commit run --all-files       # everything below but the payload lines, plus gitleaks
```

The same checks, one at a time:

```bash
uv run yamllint --strict -f github .
uv run zensical build --clean --strict        # docs site
markdownlint-cli2 '**/*.md' '!**/.venv' '!.agent'
uv run ruff check . && uv run ruff format --check .   # python only
uv run pytest -q                              # python only
cd ansible && uv run ansible-lint             # ansible only
uv run scripts/render-charts.py --out output/rendered   # payload only
uv run scripts/server-dry-run.py output/rendered        # payload only, kind
```

The render writes what Argo CD will apply; the dry run writes CRDs and
policies to its context, so it refuses anything but a throwaway `kind-*` one.

`--strict` is what CI uses. A page not registered in `nav` in `zensical.toml`
fails the build, deliberately. On a pull request, the `argo-diff-preview`
workflow comments the rendered ArgoCD manifest diff between `main` and your
branch; read it, since a three-line values change can render as two hundred
lines of different objects. The full list of workflows is in
[Maintenance](maintenance.md#ci-workflows).

## Make targets

Every target in the `Makefile`, and the page that explains it. The ones that
touch the cluster read `output/kubeconfig` (`KUBECONFIG=<file>` picks
another).

| Target | Does | Page |
| --- | --- | --- |
| `setup` | Creates the virtualenv with every tool | [Setup](#setup) |
| `download` | Fetches and verifies the Flatcar image, syslinux and the sysexts | [Quickstart](../quickstart.md#setup) |
| `config` | Renders the Ignition configs, PXE menus and credentials from the inventory | [Quickstart](../quickstart.md#setup), [Ansible](../architecture/ansible.md#playbooks) |
| `artifacts` | `download` then `config` | [Quickstart](../quickstart.md#setup) |
| `serve` | Runs the TFTP and HTTP boot server | [Boot server](../architecture/boot-process.md#boot-server) |
| `reinstall` | Arms a node (`LIMIT=<node>`) or, after a typed confirmation, every node to wipe and install on its next network boot | [Node Lifecycle](../operations/nodes.md#rebuilding-or-repartitioning-a-node) |
| `reinstall-cancel` | Disarms them again | [Switching back to local boot](../architecture/boot-process.md#switching-back-to-local-boot) |
| `kubeconfig` | Copies the admin kubeconfig to `output/kubeconfig` | [Quickstart](../quickstart.md#setup) |
| `check-context` | Guard the cluster targets depend on: fails without a kubeconfig | |
| `bootstrap` | `install-cilium`, `install-argo`, `bootstrap-apps` in one go | [Quickstart](../quickstart.md#setup) |
| `install-cilium` | Gateway API CRDs and Cilium, at the pinned versions | [Quickstart](../quickstart.md#setup) |
| `install-argo` | The argo-cd chart, at the pinned version | [Quickstart](../quickstart.md#setup) |
| `bootstrap-apps` | The AppProjects and the `argocd` Application; ArgoCD owns everything after | [Quickstart](../quickstart.md#setup) |
| `storage-check` | Walks the Rook chain and binds a test PVC | [Is storage ready?](../platform/rook-ceph.md#is-storage-ready) |
| `bao-init` | Initialises, unseals and configures OpenBao once | [OpenBao](../platform/openbao.md#bootstrap) |
| `bao-unseal` | Unseals every sealed replica from the key file (`KEYFILE=<file>` picks another) | [OpenBao](../platform/openbao.md#unsealing-after-a-restart) |
| `bao-policies` | Rewrites ESO's policies and Kubernetes auth roles | [OpenBao](../platform/openbao.md#kubernetes-auth) |
| `bao-secrets` | Prompts for the typed secrets and writes them | [OpenBao](../platform/openbao.md#bootstrap) |
| `bao-rotate-root` | Replaces a kept root token with the operator login | [OpenBao](../platform/openbao.md#operator-credential) |
| `claude-session-pat` | Stores (`SESSION=<name>`) or removes (`DELETE=1`) one agent session's GitHub token | [Claude agents](https://homelab-apps.wlkr.ch/claude-agents/) |
| `untaint`, `taint` | Removes and restores the control-plane taint | [Single-node clusters](../quickstart.md#single-node-clusters) |
| `fonts[-check]`, `mermaid[-check]` | Downloads the pinned webfonts or Mermaid, or diffs them against what is committed | [Vendored assets](#vendored-assets) |
| `clean-artifacts`, `clean` | Deletes the generated files, or everything under `output/` after asking | [Repository layout](../architecture/index.md#repository-layout) |

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

`.github/CODEOWNERS` assigns every path to the repository owner, which requests
that review; it is not required, because an author cannot approve their own
pull request. What `main` requires is the `ci-ok` check; which Renovate PRs
merge on it alone is in [Automerge policy](maintenance.md#automerge-policy).

`.agent/rules/general-rules.md` holds the standing rules for AI coding agents
working here: GitOps only, docs in `docs/`, no hand-edited versions. Keep it in
sync when those conventions change.

## Documentation

The site is built with [Zensical](https://zensical.org/) from `docs/`,
configured in `zensical.toml`, and published to GitHub Pages by
`.github/workflows/docs.yaml` at <https://homelab.wlkr.ch/>. It is
deliberately not hosted on the cluster, so it stays readable when the cluster
is not. The build also writes `llms.txt`, `llms-full.txt` and a `.md` twin of
every page for LLM clients ([llmstxt.org](https://llmstxt.org/)); the sections of
`llms.txt` are listed in `zensical.toml` under `plugins.llmstxt`, so a new
section in `nav` needs an entry there too.

| Stage | What happens |
| --- | --- |
| Build | On push to `main` touching `docs/**`, `overrides/**`, `zensical.toml`, `pyproject.toml` or `uv.lock`, `docs.yaml` runs `zensical build --clean --strict` |
| Deploy | The workflow drops a `.nojekyll` marker into `site/` (otherwise Pages runs Jekyll, which drops paths it considers private) and pushes `gh-pages` without force, sparing `pr-preview/` |
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
- Diagrams use Mermaid fences; the runtime is a [vendored asset](#vendored-assets).
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

### Vendored assets

The site serves fonts and Mermaid itself, making no third-party requests:

| Asset | Served from | Pinned in |
| --- | --- | --- |
| [Inter](https://github.com/rsms/inter/releases), [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono/releases) | `docs/assets/fonts/`, declared by `docs/stylesheets/fonts.css` (`theme.font = false`) | `scripts/update-fonts.sh` |
| [Mermaid](https://www.npmjs.com/package/mermaid) | `docs/assets/javascripts/`, loaded by `extra_javascript`, which stops the theme fetching it from unpkg.com | `scripts/update-mermaid.sh` |

Licences sit next to the files; Renovate tracks the pins.

!!! warning "A vendored-asset PR needs a second commit"
    Renovate moves the pin but cannot write the files. Check out the branch, run `make fonts` or `make mermaid`, and commit the result. `fonts-check.yaml` runs the matching `-check` target, which fails the branch until that commit arrives and so holds the automerge. Mermaid stays on the theme's major.
