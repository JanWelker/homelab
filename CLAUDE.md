# Flatcar homelab

Bare-metal Kubernetes on Flatcar Container Linux. Ansible and a PXE boot server
bring the nodes up; ArgoCD owns everything after that.

## Deployment model

`payload/` is the cluster. One hand-applied Application,
`payload/argocd/application.yaml`, syncs the argo-cd chart and
`payload/argocd/applicationset-platform.yaml`. That ApplicationSet generates one
Application per `payload/platform/*/application.yaml`, plus
`payload/workloads/application.yaml`. The latter deploys the only other
ApplicationSet, `payload/workloads/applicationset.yaml`, which generates one
Application per directory of the separate `homelab-apps` repository. There is
no app-of-apps tree.

- A component is a directory with exactly one `application.yaml` plus the
  manifests it deploys. The generator copies that file's labels, annotations,
  finalizers and `spec` verbatim, so it is a complete Application, not a
  fragment.
- Every `application.yaml` carries the same `syncPolicy`: `automated` with
  `prune` and `selfHeal`, plus the `retry` block (limit 10, 30s backoff up to
  5m). Copy it from any sibling except `kube-vip` and `security`, whose
  `prune: false` is deliberate: one holds the API VIP, the other owns
  Namespaces. ArgoCD never re-attempts a failed sync of the
  same revision without `retry`, so a component that lands a minute before
  its CRDs stays failed until `argocd app sync <app>`.
- **Nothing orders the Applications.** All of them sync at once and converge:
  a missing kind fails the sync and retries, a resource that applies but
  cannot go Ready just waits. Put a resource with what it needs, not with what
  it configures: the `ClusterSecretStore` sits with OpenBao, the issuers and
  certificates are their own `certificates` component.
- A fresh cluster looks stuck at OpenBao: every `ExternalSecret` is Degraded
  until `make bao-init`, `make bao-unseal` and `make bao-secrets`. Nothing
  fails or times out there.
- Patching a generated Application's `spec` achieves nothing — the next
  reconcile copies the file back over it. That includes switching automated
  sync off: the only rollback is a revert commit.

The reasoning is in `docs/architecture/gitops.md`.

## Editing the platform

- **Moving a resource between Applications takes two merges.** First annotate it
  `argocd.argoproj.io/sync-options: Prune=false` and
  `argocd.argoproj.io/compare-options: IgnoreExtraneous`, and let that sync —
  ArgoCD reads both off the *live* object. Then move the file. Skip it and the
  old owner deletes the resource before the new one recreates it.
- **Deleting an Application by hand: strip
  `resources-finalizer.argocd.argoproj.io` first**, or the deletion cascades
  into everything that Application manages.
- Each version is pinned once, in the `application.yaml` that owns the
  component. The `Makefile` reads `targetRevision` out of those manifests for
  the bootstrap installs — never add a second pin.
- Renovate opens one PR per component and automerges patch and minor.
  `prometheus-operator-crds` must not lag `kube-prometheus-stack`, so the two
  are grouped and arrive as one PR, as are the two Rook charts.
- Long explanations belong in `docs/`, not in YAML comments.
- Changes land through PRs off `main`.

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

`make bootstrap` runs `install-cilium` (Gateway API CRDs + Cilium) →
`install-argo` → `bootstrap-apps` (the AppProjects, then the `argocd`
Application). `make` installs only what ArgoCD needs in order to run; cert-manager,
the LoadBalancer pools and the Prometheus operator CRDs all arrive through ArgoCD.

A fresh cluster **looks stuck at OpenBao**: every `ExternalSecret` stays
Degraded until `make bao-init` and `make bao-unseal`, and `certificates` until
`make bao-secrets`. That is the design, not a hang.

`make kubeconfig` writes `output/kubeconfig`. `output/credentials/` holds the
OpenBao unseal keys and the etcd encryption key: generated once, never
regenerated identically, and `make clean` deletes them.

## Checks before pushing

```bash
uv run yamllint -f github .
uv run zensical build --clean --strict        # docs site
markdownlint-cli2 '**/*.md' '!**/.venv' '!.agent'
uv run pylint boot_server/*.py scripts/*.py   # python only
cd ansible && uv run ansible-lint             # ansible only
```
