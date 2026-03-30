# Adding a Workload

This guide walks through deploying a new application to the cluster from scratch.

## Overview

All workloads live under `payload/workloads/<app-name>/`. The `workloads` parent ArgoCD Application auto-discovers any `application.yaml` file in that directory tree, so adding a new folder is all that's needed to register the app with ArgoCD.

## Step 1: Create the App Directory

```bash
mkdir payload/workloads/my-app
```

## Step 2: Create the ArgoCD Application

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

For a Helm chart, replace `source` with a `sources` block (see existing apps like `home-assistant` for reference).

## Step 3: Add Kubernetes Manifests

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

## Step 4: Expose the App (Optional)

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

## Step 5: Handle Secrets (Optional)

If the app needs secrets, create an `InfisicalSecret` resource to sync them from Infisical into a native Kubernetes Secret. See [Infisical](../platform/infisical.md) for the full usage example.

## Step 6: Commit and Push

ArgoCD will detect the new `application.yaml` on the next sync (or immediately if auto-sync is enabled on the parent app) and deploy your workload.

```bash
git add payload/workloads/my-app/
git commit -m "feat: add my-app workload"
git push
```

## Step 7: Document It

Add a page under `payload/workloads/documentation/src/docs/workloads/my-app.md` and register it in `mkdocs.yaml` under the Workloads nav section.
