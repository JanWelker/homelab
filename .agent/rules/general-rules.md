---
trigger: always_on
---

# Repository Rules

1. Deployments are GitOps via ArgoCD, rooted at `payload/argocd/application.yaml`
   and the ApplicationSet it syncs. Nothing is applied by hand after the initial
   bootstrap. `CLAUDE.md` holds the deployment model in detail.
2. Documentation lives in `docs/`, built with Zensical and configured in
   `zensical.toml`. Every page must be registered in `nav`; the build runs with
   `--strict`.
3. Do not write version numbers into prose or bump them by hand. `targetRevision`
   in the manifests and the versions in `ansible/inventory.yaml` are the sources
   of truth, and Renovate keeps them current. The `Makefile` pins nothing: it
   reads `targetRevision` out of the manifests for the bootstrap installs.
4. Kubernetes and ArgoCD MCP servers may be available in the environment.
