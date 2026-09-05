# Contributing

Development setup, the checks that run, commit conventions, and how to validate
a `payload/` change before opening a pull request are documented at
**[Contributing](https://janwelker.github.io/homelab/development/contributing/)**.

The short version:

```bash
uv sync
uv run pre-commit install
uv run pre-commit run --all-files
uv run zensical build --clean --strict
```
