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
5. **Ship a `CiliumNetworkPolicy`**, at sync wave `-2`. Not a plain
   `NetworkPolicy` — a plain one blocks health probes and the pods restart
   forever. The reasons are in [Security
   Policies](../platform/security-policies.md#why-ciliumnetworkpolicy-and-not-networkpolicy);
   the wave is in [The network policy](#the-network-policy) and matters more
   than it looks.
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

The operator polls each instance on port `8000` from `cnpg-system` to decide
what the `Cluster` is doing, so the network policy below has to admit it. That
rule is not optional and its absence does not look like a network problem —
see the troubleshooting entry.

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
        - ingress
    # Only if a CloudNativePG Cluster is in this namespace.
    - fromEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: cnpg-system
      toPorts:
        - ports:
            - port: "8000"
              protocol: TCP
```

Wave `-2` is load-bearing, not tidiness. At the default wave the policy is
applied *after* the `Cluster` at `-1` — and `-1` is the wave that blocks
waiting for that `Cluster` to go Healthy, which it cannot do until the rule
above exists. The sync parks at `-1` forever and the rule that would release it
sits in a wave that never runs. Putting the policy with the namespace also
closes the window where the workload is running and unprotected.

Drop `ingress` from `fromEntities` if the Gateway does not reach this workload
directly — a namespace fronted by Authentik's outpost takes its traffic from
the `authentik` namespace instead.

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

### Putting a workload behind Authentik

Which of the two shapes applies depends on the application, not on preference:

**It speaks OIDC.** The `oauth2provider` blueprint goes in the *workload's* own
directory, as a ConfigMap targeted at the `authentik` namespace — see
[Where a blueprint lives](../platform/authentik.md#where-a-blueprint-lives).
Two things still come from this repository: its client credentials, generated
in `scripts/bao-secrets.sh` under the workload's own `kv/<app>/config` path —
never by adding keys to `kv/authentik/config`, which a `bao kv put` would
rewrite wholesale and take Authentik down with it — and one projected-volume
source in `payload/platform/authentik/application.yaml` so the worker mounts
it. Point the application's discovery URI at **`auth.k8s.wlkr.ch`**, not the
`infra` name: workloads are reachable from outside the local network and that
one is not. See [Two hostnames](../platform/authentik.md#two-hostnames).

**It does not.** Add a `proxyprovider` and list it on the embedded outpost, then
point the workload's `HTTPRoute` `backendRef` at `authentik-server` in the
`authentik` namespace instead of at the application. That is a cross-namespace
reference, so the workload's namespace also has to be added to
`payload/platform/authentik/referencegrant.yaml`.

Both are changes to **this** repository, which means a workload going behind
SSO is two pull requests. That friction is deliberate: a workload should not be
able to put itself behind — or quietly take itself out from behind — the
cluster's authentication layer.

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

**The Application is stuck `Synced` with health `Unknown`, and the workload is
running fine.** A wave is waiting on a resource that will never go Healthy.
With a database it is almost always the missing `cnpg-system` rule: Postgres
serves the application, `kubectl get cluster` says `1/1` ready, but the status
reads `Instance Status Extraction Error: HTTP communication issue` because the
operator cannot reach port `8000`.

```bash
kubectl -n argocd get application my-app \
  -o jsonpath='{.status.operationState.message}'
# waiting for healthy state of postgresql.cnpg.io/Cluster/my-app-db
```

Health `Unknown` with no resource naming itself is the signature — a genuinely
unhealthy workload says which resource is failing. Confirm the drop on the node
running the database:

```bash
kubectl -n kube-system exec <cilium-pod-on-that-node> -c cilium-agent -- \
  hubble observe --verdict DROPPED --from-namespace cnpg-system --last 20
```

If the rule is already in Git and nothing changes, check the policy's sync wave
before re-syncing anything. A policy at the default wave is applied after the
`Cluster` it unblocks, so every attempt stops in the same place; the applied
list confirms it:

```bash
kubectl -n argocd get application my-app -o jsonpath=\
'{range .status.operationState.syncResult.resources[*]}{.kind}{"\t"}{.hookPhase}{"\n"}{end}'
```

No `CiliumNetworkPolicy` in that list while `Cluster` reads `Running` means the
wave is wrong, not the rule. Fix the wave, then `argocd app terminate-op
my-app` — the stuck operation does not pick up a new revision on its own.

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
