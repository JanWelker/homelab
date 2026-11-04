---
trigger: always_on
---

# Repository Rules

1. Deployments are GitOps via ArgoCD; nothing is applied by hand after the
   initial bootstrap. `CLAUDE.md` has the short version,
   `docs/architecture/gitops.md` the model.
2. Documentation lives in `docs/`, built with Zensical from `zensical.toml`
   with `--strict`. Both documentation sites are LLM-readable:
   <https://homelab.wlkr.ch/llms-full.txt> and
   <https://homelab-apps.wlkr.ch/llms-full.txt>.
3. Do not write version numbers into prose or bump them by hand; Renovate
   moves the pins ([Version pins](../../docs/architecture/gitops.md#version-pins)).
4. Kubernetes and ArgoCD MCP servers may be available in the environment.
