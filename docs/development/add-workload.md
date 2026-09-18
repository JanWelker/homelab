---
description: "Deploy a new application to the cluster, from the ArgoCD Application and manifests through to exposing and documenting it."
---

# Adding a Workload

This guide walks through deploying a new application to the cluster from
scratch. The good news: once the first one exists, adding the next is creating a
directory and pushing. The whole point of the machinery in the rest of this
documentation is that this page is short.

## Overview

All workloads live under `payload/workloads/<app-name>/`, one directory per app with an `application.yaml` in it. The `platform` ApplicationSet generates an ArgoCD Application from every such file it is told to look at, so adding a new folder is all that's needed to register an app. No console, no `kubectl apply`, no step that only exists in someone's memory.

!!! note
    There are currently no workloads, so `payload/workloads/` does not exist. The first workload needs Step 1 below; subsequent ones can skip it.

## Step 1: Let the ApplicationSet see the workloads

In `payload/argocd/applicationset.yaml`, add the workloads path to the generator
and a step for them at the end of the rollout:

```yaml
  generators:
    - git:
        repoURL: https://github.com/JanWelker/homelab.git
        revision: HEAD
        files:
          - path: payload/platform/*/application.yaml
          - path: payload/workloads/*/application.yaml
  strategy:
    type: RollingSync
    rollingSync:
      steps:
        # ... the existing steps, then:
        - matchExpressions:
            - {key: homelab.wlkr.ch/stage, operator: In, values: [12-workloads]}
```

Workloads go last, after `11-policy`: they need the platform under them, and a
broken workload should not hold back anything the cluster runs on.

The `argocd` Application syncs `payload/argocd/`, so merging the change is
enough — no `kubectl apply`.

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
  labels:
    # Without a stage the ApplicationSet fails rather than skipping the app.
    homelab.wlkr.ch/stage: 12-workloads
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

!!! note
    A namespace with a policy is default-deny for ingress once one selects its pods, while every other namespace stays open. If you write one, use a `CiliumNetworkPolicy` rather than a plain `NetworkPolicy` — the reasons are in [Security Policies](../platform/security-policies.md#why-ciliumnetworkpolicy-and-not-networkpolicy), and the short version is that a plain one blocks health probes and your pods will restart forever. See also [Security Posture](../architecture/security.md#authorization).

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
      sectionName: https
  hostnames:
    - "my-app.k8s.wlkr.ch"
  rules:
    - backendRefs:
        - name: my-app-svc
          port: 80
```

See [Gateway API](../platform/gateway-api.md) for more details.

## Step 6: Commit and Push

The ApplicationSet picks up the new `application.yaml` on its next reconcile and generates the Application; it syncs once the stages before it are Healthy. The hostname's DNS record and TLS certificate are already handled — [external-dns](../platform/external-dns.md) publishes the record from the `HTTPRoute`, and the Gateway's wildcard certificate covers the name. Neither needs a step of its own.

```bash
git add payload/workloads/my-app/
git commit -m "feat: add my-app workload"
git push
```

## Step 7: Document It

Not optional, and not busywork. An undocumented workload is one you will
rediscover in eighteen months by reading YAML and guessing. Add a page at
`docs/workloads/my-app.md` and register it in `zensical.toml`. There is no
Workloads section in the `nav` yet — the first workload creates it, after
Platform and before Operations:

```toml
  { "Workloads" = [
    { "My App" = "workloads/my-app.md" },
  ] },
```

The build runs with `--strict`, so an unregistered page fails CI.
