# Flatcar homelab

Bare-metal Kubernetes on Flatcar Container Linux. Ansible and a PXE boot server
bring the nodes up; ArgoCD owns everything after that.

## Deployment model

`payload/` is the cluster. One hand-applied Application,
`payload/argocd/application.yaml`, syncs the argo-cd chart and
`payload/argocd/applicationset.yaml`. That ApplicationSet generates one
Application per `payload/platform/*/application.yaml`. Nothing else creates
Applications — there is no app-of-apps tree.

- A component is a directory with exactly one `application.yaml` plus the
  manifests it deploys. The generator copies that file's labels, annotations,
  finalizers and `spec` verbatim, so it is a complete Application, not a
  fragment.
- Every `application.yaml` **must** carry a `homelab.wlkr.ch/stage` label. A
  missing one fails the whole ApplicationSet rather than quietly skipping the
  app.
- Stages sync in order, and one starts only when every Application in the
  previous stage is Synced **and** Healthy: `00-projects`, `01-crds`,
  `02-network`, `03-controllers`, `04-storage`, `05-secrets`, `06-certificates`,
  `07-ingress`, `08-services`, `09-backends`, `10-agents`, `11-policy`.
- **Nothing may depend on a later stage.** A resource that cannot apply, or
  cannot go Healthy, until later deadlocks its stage. That is why the
  `ClusterSecretStore` sits with OpenBao, the issuers and certificates are their
  own `certificates` component, and the HTTPRoutes in `cilium`, `rook-ceph` and
  `openbao` carry `argocd.argoproj.io/ignore-healthcheck`.
- `RollingSync` switches auto-sync off on generated Applications: no self-heal,
  and a sync that exhausts its `retry` waits for `argocd app sync <app>`.
  Patching a generated Application's `spec` achieves nothing — the next
  reconcile copies the file back over it. Only `argocd` itself keeps `selfHeal`.

The reasoning is in `docs/architecture/gitops.md`.

## Editing the platform

- **Moving a resource between Applications takes two merges.** First annotate it
  `argocd.argoproj.io/sync-options: Prune=false` and
  `argocd.argoproj.io/compare-options: IgnoreExtraneous`, and let that sync —
  ArgoCD reads both off the *live* object. Then move the file. Skip it and the
  old owner either deletes the resource or stays OutOfSync and blocks its stage.
- **Deleting an Application by hand: strip
  `resources-finalizer.argocd.argoproj.io` first**, or the deletion cascades
  into everything that Application manages.
- Each version is pinned once, in the `application.yaml` that owns the
  component. The `Makefile` reads `targetRevision` out of those manifests for
  the bootstrap installs — never add a second pin.
- Renovate opens one PR per component and automerges patch and minor.
  `prometheus-operator-crds` must not lag `kube-prometheus-stack`: merge the CRD
  bump first, or both together.
- Long explanations belong in `docs/`, not in YAML comments.
- Changes land through PRs off `main`.

## Bootstrap

`make bootstrap` runs `install-cilium` (Gateway API CRDs + Cilium) →
`install-argo` → `bootstrap-apps` (the AppProjects, then the `argocd`
Application). `make` installs only what ArgoCD needs in order to run; cert-manager,
the LoadBalancer pools and the Prometheus operator CRDs all arrive through ArgoCD.

A fresh cluster **pauses at `05-secrets`** until `make bao-init` and
`make bao-unseal`, then again at `06-certificates` until `make bao-secrets`.
That is the design, not a hang.

`make kubeconfig` writes `output/kubeconfig`. `output/credentials/` holds the
OpenBao unseal keys and the etcd encryption key: generated once, never
regenerated identically, and `make clean` deletes them.

## Checks before pushing

```bash
uv run yamllint -f github .
uv run zensical build --clean --strict        # docs site
markdownlint-cli2 '**/*.md' '!**/.venv' '!.agent'
uv run pylint boot_server/*.py                # boot server only
cd ansible && uv run ansible-lint             # ansible only
```
