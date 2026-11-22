# Adding a Workload

Workloads live in [`JanWelker/homelab-apps`](<https://github.com/JanWelker/homelab-apps>), not here. The `apps` ApplicationSet generates one ArgoCD Application per `*/application.yaml` in that repository, so adding an application is creating a directory there and pushing it. Why the split exists is in [GitOps Strategy](<https://homelab.wlkr.ch/architecture/gitops/index.md#workloads-live-in-a-second-repository>).

## Rules and platform-side changes

Every directory follows the workloads repository's [conventions](<https://homelab-apps.wlkr.ch/conventions/>), which also list what the platform does without being asked. Two needs reach into this repository first:

| A workload with | Needs here first |
| --- | --- |
| A chart from a new repository | The host in the `apps` project's `sourceRepos` (`payload/platform/argocd-projects/projects.yaml`) and in the repo-server's egress policy (`payload/platform/security/network-policies/argocd.yaml`), or the Application reads `ComparisonError` |
| Secrets from OpenBao | Its namespace on the `openbao-apps` list in `payload/platform/openbao/cluster-secret-store.yaml`, then `make bao-policies` — see [Adding a secret](<https://homelab.wlkr.ch/platform/external-secrets/index.md#adding-a-secret>) |

## Step 1: Create the directory

```bash
git clone git@github.com:JanWelker/homelab-apps.git
cd homelab-apps
mkdir my-app
```

## Step 2: Write the Application

`my-app/application.yaml`, for an app built from plain manifests:

```yaml
---
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
    repoURL: https://github.com/JanWelker/homelab-apps.git
    targetRevision: HEAD
    path: my-app
    directory:
      exclude: "application.yaml"
  destination:
    server: https://kubernetes.default.svc
    namespace: my-app
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    retry:
      limit: 10
      backoff:
        duration: 30s
        factor: 2
        maxDuration: 5m
    syncOptions:
      # No CreateNamespace: namespace.yaml below creates it, with the Pod
      # Security labels an implicitly created one would not have.
      - ServerSideApply=true
```

For a Helm chart that also needs manifests of its own, use a `sources` list with the chart in one entry and this repository in another; `dependency-track/application.yaml` is the worked example. The `syncPolicy` is the one every platform Application carries, `retry` included; without it a workload that referenced a platform resource a minute too early stays failed — see [Sync policy](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>).

## Step 3: Add the manifests

```text
my-app/
├── application.yaml
├── namespace.yaml      # Pod Security labels, sync wave -2
├── database.yaml       # CloudNativePG Cluster, sync wave -1, if it needs one
├── backup.yaml         # its backup stanza, sync wave -2, with the database
├── deployment.yaml
├── service.yaml
├── httproute.yaml      # if you want it reachable
└── networkpolicy.yaml  # CiliumNetworkPolicy, sync wave -2
```

### The namespace

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: my-app
  labels:
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
  annotations:
    # Pruning a Namespace deletes the PVCs inside it.
    argocd.argoproj.io/sync-options: Prune=false
    argocd.argoproj.io/sync-wave: "-2"
```

Set `enforce` to what the image demonstrably needs (`restricted` if it runs non-root, `baseline` if not) and leave `audit` and `warn` stricter so the gap stays visible.

### If it needs a database

```yaml
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: my-app-db
  namespace: my-app
  annotations:
    argocd.argoproj.io/sync-options: Prune=false
    argocd.argoproj.io/sync-wave: "-1"
spec:
  instances: 1
  smartShutdownTimeout: 15
  enablePDB: false   # one instance only
  storage:
    size: 10Gi
    storageClass: rook-ceph-block
  bootstrap:
    initdb:
      database: my-app
      owner: my-app
  plugins:
    - name: barman-cloud.cloudnative-pg.io
      enabled: true
      isWALArchiver: true
      parameters:
        barmanObjectName: my-app-db
```

`backup.yaml` is the stanza in [Backing up a database](<https://homelab.wlkr.ch/platform/cloudnative-pg/index.md#backing-up-a-database>) with the names filled in; the `cnpg-cluster-backed-up` admission policy refuses a `Cluster` without the `plugins` entry. The operator polls each instance on port `8000` from `cnpg-system`, so the network policy below must admit it.

### The network policy

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: default-ingress
  namespace: my-app
  annotations:
    # With the namespace, ahead of the database at -1.
    argocd.argoproj.io/sync-wave: "-2"
spec:
  endpointSelector: {}
  ingress:
    # Within the namespace, and the node for health probes.
    - fromEndpoints:
        - {}
    - fromEntities:
        - host
        - remote-node
    # The Gateway, on the container port behind the Service the HTTPRoute
    # targets: a policy matches the pod's port, not the Service's.
    - fromEntities:
        - ingress
      toPorts:
        - ports:
            - port: "8080"
              protocol: TCP
    # Only if a CloudNativePG Cluster is in this namespace.
    - fromEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: cnpg-system
      toPorts:
        - ports:
            - port: "8000"
              protocol: TCP
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: default-egress
  namespace: my-app
  annotations:
    argocd.argoproj.io/sync-wave: "-2"
spec:
  endpointSelector: {}
  egress:
    # Within the namespace. DNS is not listed: the cluster-wide policy in
    # payload/platform/security/network-policies/ sends every pod's
    # lookups through the proxy, which is what makes toFQDNs work.
    - toEndpoints:
        - {}
    # Every name the application dials, by name. Authentik counts: its
    # hostname resolves to the Gateway's own address, which is world.
    - toFQDNs:
        - matchName: auth.k8s.wlkr.ch
      toPorts:
        - ports:
            - port: "443"
              protocol: TCP
---
# Only if a CloudNativePG Cluster is in this namespace: the instance
# manager and the join and initdb Jobs, which lack the podRole label, read
# the API server.
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: database
  namespace: my-app
  annotations:
    argocd.argoproj.io/sync-wave: "-2"
spec:
  endpointSelector:
    matchLabels:
      cnpg.io/cluster: my-app-db
  egress:
    - toEntities:
        - kube-apiserver
```

Wave `-2` is load-bearing: at the default wave the policy is applied after the `Cluster` at `-1`, which cannot go Healthy until the `cnpg-system` rule exists, so the sync parks at `-1` forever. Drop the `ingress` rule if the Gateway does not reach this workload directly (a namespace fronted by Authentik's outpost takes its traffic from the `authentik` namespace). A Prometheus rule belongs here only with a ServiceMonitor, on that port, with `rules.http`; the shape and the rollout are in [Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#network-policies>).

### Exposing it

```yaml
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-app
  namespace: my-app
spec:
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: apps-gateway
      namespace: kube-system
      sectionName: https
  hostnames:
    - "my-app.k8s.wlkr.ch"
  rules:
    - backendRefs:
        - name: my-app
          port: 80
```

DNS and TLS need nothing further — see [Gateway API](<https://homelab.wlkr.ch/platform/gateway-api/index.md>).

### Putting a workload behind Authentik

- **It speaks OIDC:** the `oauth2provider` blueprint is a key of `payload/platform/authentik/blueprints-workloads.yaml`; its client credentials come from a `PushSecret` in the workload's directory writing `kv/<app>/config` — see [Generated secrets](<https://homelab.wlkr.ch/platform/openbao/index.md#generated-secrets>); the discovery URI is `auth.k8s.wlkr.ch`.
- **It does not:** add a `proxyprovider` on the embedded outpost, point the `HTTPRoute` at `authentik-server` in the `authentik` namespace, add the namespace to `payload/platform/authentik/referencegrant.yaml`, and allow the outpost to reach the pods in the `authentik-server` egress policy.

Either way it is two pull requests, one here, because a workload must not be able to put itself behind or take itself out from behind the cluster's authentication layer. Details in [Authentik](<https://homelab.wlkr.ch/platform/authentik/index.md>).

## Step 4: Open the pull request

```bash
git checkout -b feat/my-app
git add my-app/
git commit -m "feat: add my-app"
git push -u origin feat/my-app
gh pr create --fill
```

Once it merges, the ApplicationSet picks the directory up within its requeue interval (`requeueAfterSeconds` in `payload/workloads/applicationset.yaml`), or immediately after **Refresh** on the `apps` ApplicationSet in the ArgoCD UI.

## Step 5: Document it

The page goes in the workloads repository at `docs/my-app.md`, registered in its `zensical.toml` under both `nav` and `plugins.llmstxt.sections`, and published to [homelab-apps.wlkr.ch](<https://homelab-apps.wlkr.ch/>); the `--strict` build fails on an unregistered page. It follows the shared skeleton: intro, At a glance, Configuration, Usage, Health check, Pitfalls, and Recovery where a real runbook exists.

## Troubleshooting

| Symptom | Cause and check |
| --- | --- |
| The Application was never generated | Only `*/application.yaml`, one level deep, is read, and a `spec.project` other than `apps` fails the whole set naming the file. Read the controller log (below) |
| The Application exists but will not sync | It has exhausted its retries, on a fresh cluster usually against a platform resource that arrived later than the retry budget. `argocd app sync <app>` — see [Sync policy](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>) |
| `Synced`, health `Unknown`, workload running fine | A wave is waiting on a resource that will never go Healthy, with a database usually the `cnpg-system` rule or its wave — see [Sync stuck on the database](<https://homelab-apps.wlkr.ch/conventions/#sync-stuck-on-the-database>) |
| Pods will not start; events mention a security policy | `enforce` in `namespace.yaml` is stricter than the image needs. Loosen `enforce`, keep `audit` and `warn`, so violations stay in the [audit log](<https://homelab.wlkr.ch/architecture/audit-logging/index.md>) — see [Pod Security Admission](<https://homelab.wlkr.ch/platform/security-policies/index.md#pod-security-admission>) |
| Login redirects in a loop, or every link is `http://` | Requests arrive from Cilium's Envoy inside the [pod subnet](<https://homelab.wlkr.ch/architecture/index.md#cluster-layout>) with TLS terminated at the Gateway. Set the application's trusted-proxy and "overwrite protocol" settings |

```bash
# Why an Application was not generated
kubectl -n argocd logs deploy/argocd-applicationset-controller | tail -50
```
