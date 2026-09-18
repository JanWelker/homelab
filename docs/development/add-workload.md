---
description: "Deploy an application to the cluster: the workloads repository, the rules every app directory follows, and what the platform gives you for free."
---

# Adding a Workload

Workloads live in their own repository,
[`JanWelker/homelab-apps`](https://github.com/JanWelker/homelab-apps), not in
this one. Adding an application is creating a directory there and pushing it —
no console, no `kubectl apply`, no step that exists only in someone's memory.
[GitOps Strategy](../architecture/gitops.md#workloads-live-in-a-second-repository)
covers why the split exists and what it costs.

The `apps` ApplicationSet generates one ArgoCD Application per
`*/application.yaml` in that repository. There is no list to register a new
application in; the directory *is* the registration.

## What the platform already does for you

This is the part worth internalising before writing any YAML, because most of
what an application needs is already handled and duplicating it is the usual
mistake:

| You want | You write | The platform does |
| --- | --- | --- |
| A hostname | An `HTTPRoute` with `hostnames: [app.k8s.wlkr.ch]` | [external-dns](../platform/external-dns.md) publishes the Route53 record from the route |
| HTTPS | Nothing | The `apps-gateway` already terminates TLS with a `*.k8s.wlkr.ch` wildcard |
| Storage | A PVC with `storageClassName: rook-ceph-block` | [Rook-Ceph](../platform/rook-ceph.md) replicates it three ways, one copy per host |
| A database | A CloudNativePG `Cluster` | [The operator](../platform/cloudnative-pg.md) generates the credentials and runs the failover |
| A secret from outside the cluster | An `ExternalSecret` | [OpenBao](../platform/openbao.md) holds it; ESO renders a native `Secret` |
| Metrics | A `ServiceMonitor` or `PodMonitor` | Prometheus scrapes it, Grafana can chart it |
| Logs | Nothing | Alloy ships every container's stdout to Loki |
| Backups | Nothing, for a PVC | Velero snapshots it on the cluster schedule |

## The rules

Every directory in the workloads repository follows these. They are restated in
its `CONVENTIONS.md`, which is the copy to keep current.

1. **Exactly one `application.yaml` per directory**, and it is a complete
   ArgoCD `Application`, not a fragment. The generator reads the file and
   copies its labels, annotations, finalizers and `spec` through unchanged.
2. **`project: apps`.** The ApplicationSet refuses to generate anything else —
   a workload in the `infra` or `system` project would be authorised against
   destinations it has no business in, so the whole set fails rather than let
   one through.
3. **The workload owns its namespace.** Ship a `namespace.yaml` at sync wave
   `-2` carrying the three Pod Security Admission labels, and annotate it
   `Prune=false` — pruning a `Namespace` deletes every PVC inside it. A
   namespace nobody labelled runs at `privileged`, which enforces nothing, and
   that is exactly what `CreateNamespace=true` on its own leaves behind.
4. **PostgreSQL is a CloudNativePG `Cluster`.** Never a chart's bundled
   database. See [the contract](../platform/cloudnative-pg.md#the-contract).
5. **Ship a `CiliumNetworkPolicy`.** Not a plain `NetworkPolicy` — a plain one
   blocks health probes and the pods restart forever. The reasons are in
   [Security Policies](../platform/security-policies.md#why-ciliumnetworkpolicy-and-not-networkpolicy).
6. **Official upstream sources only.** The vendor's own chart or the vendor's
   own image. Not a repackager's chart, however convenient — the whole point of
   pinning a version is knowing who published it.
7. **Pin every version**, and let Renovate move it. No `latest`, no floating
   tags.

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
    syncOptions:
      # No CreateNamespace: namespace.yaml below creates it, with the Pod
      # Security labels an implicitly created one would not have.
      - ServerSideApply=true
```

For a Helm chart that also needs manifests of its own — a database, an
`HTTPRoute`, a network policy — use a `sources` list with the chart in one
entry and this repository in another. `nextcloud/application.yaml` is the
worked example.

!!! tip "Workloads keep selfHeal"
    Platform Applications give up `selfHeal` to the `RollingSync` strategy;
    workloads do not. A hand-edited workload Deployment is reverted within
    minutes, which is the behaviour most people expect from ArgoCD in the first
    place.

## Step 3: Add the manifests

```text
my-app/
├── application.yaml
├── namespace.yaml      # Pod Security labels, sync wave -2
├── database.yaml       # CloudNativePG Cluster, sync wave -1, if it needs one
├── deployment.yaml
├── service.yaml
├── httproute.yaml      # if you want it reachable
└── networkpolicy.yaml
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

Set `enforce` to what the image demonstrably needs — `restricted` if it runs
as a non-root user, `baseline` if it does not — and leave `audit` and `warn`
stricter so the gap stays visible.

### If it needs a database

```yaml
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: my-app-db
  namespace: my-app
  annotations:
    # Before the application: ArgoCD has a health check for this kind and
    # waits for "Cluster in healthy state" before starting the next wave.
    argocd.argoproj.io/sync-wave: "-1"
spec:
  instances: 1
  storage:
    size: 10Gi
    storageClass: rook-ceph-block
  bootstrap:
    initdb:
      database: my-app
      owner: my-app
```

That produces a Secret called `my-app-db-app` holding `username`, `password`,
`host`, `port`, `dbname` and a ready-assembled `uri`. Point the application at
those keys; never copy the value anywhere.

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
    - name: apps-gateway
      namespace: kube-system
      sectionName: https
  hostnames:
    - "my-app.k8s.wlkr.ch"
  rules:
    - backendRefs:
        - name: my-app
          port: 80
```

DNS and TLS need nothing further. See [Gateway API](../platform/gateway-api.md).

## Step 4: Commit and push

```bash
git add my-app/
git commit -m "feat: add my-app"
git push
```

The ApplicationSet picks the directory up on its next reconcile — within about
three minutes, or immediately if you hit **Refresh** on the `apps`
ApplicationSet in the ArgoCD UI.

## Step 5: Document it

Not optional, and not busywork: an undocumented workload is one you will
rediscover in eighteen months by reading YAML and guessing.

The page goes in the **workloads** repository, next to the manifests it
describes, at `docs/my-app.md` — registered in that repository's own
`zensical.toml`, which publishes to
[janwelker.github.io/homelab-apps](https://janwelker.github.io/homelab-apps/).
Documentation lives with what it documents, so a pull request that changes a
manifest can change its page in the same diff. Both sites build with
`--strict`, so an unregistered page fails CI.

Say what the application is for, which hostname it answers on, what it stores
and where, and anything about it that surprised you while deploying it. That
last category is the one that pays for the page.

## Troubleshooting

**The Application was never generated.** The ApplicationSet only reads files
matching `*/application.yaml` — one level deep, exactly that name. Check the
controller:

```bash
kubectl -n argocd logs deploy/argocd-applicationset-controller | tail -50
```

A workload whose `spec.project` is not `apps` fails the whole set with a
message naming the file, rather than silently skipping it.

**The Application exists but will not sync.** On a fresh cluster, check that
`12-workloads` has been reached at all — the `apps` ApplicationSet does not
exist before then:

```bash
kubectl -n argocd get applicationset platform \
  -o jsonpath='{range .status.applicationStatus[*]}{.step}{"\t"}{.status}{"\t"}{.application}{"\n"}{end}'
```

**The pods will not start, and the events mention a security policy.** The
`enforce` level in the workload's own `namespace.yaml` is stricter than the
image needs. Both applications deployed so far sit at `baseline`, because
neither upstream supports running as a non-root user. Loosen `enforce` to what
the image demonstrably needs and leave `audit` and `warn` where they are, so
the violations stay visible in the [audit
log](../architecture/audit-logging.md) — see [Security
Policies](../platform/security-policies.md#pod-security-admission) for why the
three labels are split that way.

**The login redirects in a loop, or every link is `http://`.** The application
is behind the `apps-gateway`, so requests arrive from Cilium's Envoy inside the
pod CIDR (`10.244.0.0/16`) and TLS was terminated at the Gateway. Find the
application's trusted-proxy and "overwrite protocol" settings; both workloads
in the repository configure them, and neither works without.
