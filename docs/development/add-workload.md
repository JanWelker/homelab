# Adding a Workload

This guide walks through deploying a new application to the cluster from scratch.

## Overview

All workloads live under `payload/workloads/<app-name>/`. A `workloads` parent ArgoCD Application auto-discovers any `application.yaml` file in that directory tree, so once the parent exists, adding a new folder is all that's needed to register an app with ArgoCD.

!!! note
    There are currently no workloads, so `payload/workloads/` and its parent
    Application do not exist. The first workload needs Step 1 below; subsequent
    ones can skip it.

## Step 1: Restore the Workloads Parent Application

Add this document back to `payload/root.yaml` (the `apps` AppProject it
references is still defined in `payload/argocd/argocd-projects.yaml`):

```yaml
---
# Parent Application: manages user workloads from payload/workloads/
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: workloads
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: apps
  source:
    repoURL: https://github.com/JanWelker/homelab.git
    targetRevision: HEAD
    path: payload/workloads
    directory:
      recurse: true
      include: "{**/application.yaml}"
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

`payload/root.yaml` is applied manually, so run `make bootstrap-apps` (or
`kubectl apply -f payload/root.yaml`) once after adding it.

## Step 2: Create the App Directory

```bash
mkdir -p payload/workloads/my-app
```

## Step 3: Create the ArgoCD Application

Create `payload/workloads/my-app/application.yaml`. For a plain manifest-based app:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argocd
  annotations:
    argocd.argoproj.io/manifest-generate-paths: .
spec:
  project: apps
  source:
    repoURL: https://github.com/JanWelker/homelab.git
    targetRevision: HEAD
    path: payload/workloads/my-app
    directory:
      exclude: "application.yaml"
  destination:
    server: https://kubernetes.default.svc
    namespace: my-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

For a Helm chart, replace `source` with a `sources` block.

## Step 4: Add Kubernetes Manifests

At minimum you need a Deployment and a Service. Place them alongside `application.yaml`:

```text
payload/workloads/my-app/
├── application.yaml
├── namespace.yaml      # optional if using CreateNamespace=true
├── deployment.yaml
├── service.yaml
├── httproute.yaml      # if you want to expose the app
└── networkpolicy.yaml  # recommended
```

## Step 5: Expose the App (Optional)

Create `httproute.yaml` to route traffic from the `apps-gateway`:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-app
  namespace: my-app
spec:
  parentRefs:
    - name: apps-gateway
      namespace: kube-system
  hostnames:
    - "my-app.k8s.wlkr.ch"
  rules:
    - backendRefs:
        - name: my-app-svc
          port: 80
```

See [Gateway API](../platform/gateway-api.md) for more details.

## Step 6: Commit and Push

ArgoCD will detect the new `application.yaml` on the next sync (or immediately if auto-sync is enabled on the parent app) and deploy your workload.

```bash
git add payload/workloads/my-app/
git commit -m "feat: add my-app workload"
git push
```

## Step 7: Document It

Add a page at `docs/workloads/my-app.md` and register it in `zensical.toml`
under the Workloads `nav` section:

```toml
  { "Workloads" = [
    { "Overview" = "workloads/index.md" },
    { "My App" = "workloads/my-app.md" },
  ] },
```

The build runs with `--strict`, so an unregistered page fails CI.
