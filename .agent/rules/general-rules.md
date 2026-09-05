---
trigger: always_on
---

# Repository Rules

1. Deployments are GitOps via ArgoCD, rooted at `payload/root.yaml`. Nothing is
   applied by hand after the initial bootstrap.
2. Documentation lives in `docs/`, built with Zensical and configured in
   `zensical.toml`. Every page must be registered in `nav`; the build runs with
   `--strict`.
3. Do not write version numbers into prose or bump them by hand. `targetRevision`
   in the manifests and the versions in `ansible/inventory.yaml` are the sources
   of truth, and Renovate keeps them current. The exception is the `Makefile`,
   which pins the components installed before ArgoCD exists.
4. Kubernetes and ArgoCD MCP servers may be available in the environment.
