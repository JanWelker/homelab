---
description: "How this documentation site is built with Zensical, published to GitHub Pages, and previewed locally."
---

# Documentation System

The project documentation (this site) is built with
[Zensical](https://zensical.org/) and published to GitHub Pages by a GitHub
Actions workflow.

## Workflow

1. **Authoring**: Documentation is written in Markdown within `docs/`. The site
    is configured in `zensical.toml` at the repository root.
2. **Build**: On push to `main`, the `docs.yaml` workflow runs
    `zensical build --clean --strict`, producing a static site in `site/`.
3. **Upload**: The build output is uploaded as a GitHub Pages artifact.
4. **Deploy**: `actions/deploy-pages` publishes the artifact to
    <https://janwelker.github.io/homelab/>.

No container image, registry, or cluster is involved — the docs stay available
independently of the homelab.

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Git as GitHub Repo
    participant GA as GitHub Actions
    participant Pages as GitHub Pages

    Dev->>Git: Push Changes (docs/**, zensical.toml)
    Git->>GA: Trigger "Publish Documentation"
    GA->>GA: zensical build --clean --strict
    GA->>GA: Upload Pages artifact
    GA->>Pages: Deploy artifact
    Pages-->>Dev: janwelker.github.io/homelab
```

## Working locally

Zensical is declared in the `dev` dependency group of `pyproject.toml`:

```bash
uv sync --dev
uv run zensical serve   # http://localhost:8000, rebuilds on save
uv run zensical build   # one-off build into site/
```

`site/` is gitignored.

## Conventions

- Every page must be reachable from the `nav` in `zensical.toml`; the build runs
    with `--strict`, so broken links and orphaned pages fail CI.
- Cross-references use relative Markdown paths (for example
    `platform/openbao.md`, `../quickstart.md`) so they resolve both on the site
    and when browsing the repository on GitHub.
- Diagrams use Mermaid fences; Zensical initialises the runtime automatically on
    pages that contain one.
- Markdown is linted by `lint-markdown.yaml` and the `markdownlint-cli2`
    pre-commit hook.
