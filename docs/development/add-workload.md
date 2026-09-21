---
description: "Deploy an application to the cluster: the workloads repository, the rules every app directory follows, and what the platform gives you for free."
---

# Adding a Workload

Workloads live in [`JanWelker/homelab-apps`](https://github.com/JanWelker/homelab-apps),
not here. The `apps` ApplicationSet generates one ArgoCD Application per
`*/application.yaml` in that repository, so adding an application is creating a
directory there and pushing it. Why the split exists is in
[GitOps Strategy](../architecture/gitops.md#workloads-live-in-a-second-repository).

## What the platform already does for you

| You want | You write | The platform does |
| --- | --- | --- |
| A hostname | An `HTTPRoute` with `hostnames: [app.k8s.wlkr.ch]` | [external-dns](../platform/external-dns.md) publishes the Route53 record |
| HTTPS | Nothing | `apps-gateway` terminates TLS with a `*.k8s.wlkr.ch` wildcard |
| Storage | A PVC with `storageClassName: rook-ceph-block` | [Rook-Ceph](../platform/rook-ceph.md) replicates it three ways, one copy per host |
| A database | A CloudNativePG `Cluster` | [The operator](../platform/cloudnative-pg.md) generates credentials and runs failover |
| A secret from outside the cluster | An `ExternalSecret` | [OpenBao](../platform/openbao.md) holds it; ESO renders a `Secret` |
| Metrics | A `ServiceMonitor` or `PodMonitor` | Prometheus scrapes it |
| Logs | Nothing | Alloy ships every container's stdout to Loki |
| Backups | Nothing, for a PVC | Velero snapshots it nightly |

## The rules

Restated in the workloads repository's `CONVENTIONS.md`, which is the copy to
keep current.

1. **Exactly one `application.yaml` per directory**, a complete ArgoCD
   `Application`: the generator copies its labels, annotations, finalizers and
   `spec` through unchanged.
2. **`project: apps`.** Anything else fails the whole ApplicationSet. The
   project, in `payload/platform/argocd-projects/projects.yaml`, lists the chart
   repositories workloads may pull from; a new chart repository extends that
   list first, in this repository.
3. **The workload owns its namespace.** Ship a `namespace.yaml` at sync wave
   `-2` with the three Pod Security Admission labels, annotated `Prune=false`,
   because pruning a `Namespace` deletes every PVC inside it. A namespace
   created by `CreateNamespace=true` has no labels and enforces nothing.
4. **PostgreSQL is a CloudNativePG `Cluster`**, never a chart's bundled
   database — see [the contract](../platform/cloudnative-pg.md#the-contract).
5. **Ship a `CiliumNetworkPolicy`** at sync wave `-2`, not a plain
   `NetworkPolicy`, which blocks health probes — see
   [Security Policies](../platform/security-policies.md#why-ciliumnetworkpolicy-and-not-networkpolicy).
6. **Official upstream sources only**: the vendor's chart or image, not a
   repackager's.
7. **Pin every version** and let Renovate move it. No `latest`.

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

For a Helm chart that also needs manifests of its own, use a `sources` list
with the chart in one entry and this repository in another;
`nextcloud/application.yaml` is the worked example. The `syncPolicy` is the
one every platform Application carries, `retry` included; without it a
workload that referenced a platform resource a minute too early stays failed
— see [Sync policy](../architecture/gitops.md#sync-policy).

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

Set `enforce` to what the image demonstrably needs (`restricted` if it runs
non-root, `baseline` if not) and leave `audit` and `warn` stricter so the gap
stays visible.

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

That produces a Secret `my-app-db-app` holding `username`, `password`, `host`,
`port`, `dbname` and a ready-assembled `uri`; point the application at those
keys. The operator polls each instance on port `8000` from `cnpg-system`, so
the network policy below must admit it.

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
    # The Gateway, on the port the HTTPRoute targets.
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
# manager reports to the API server.
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
      cnpg.io/podRole: instance
  egress:
    - toEntities:
        - kube-apiserver
```

Wave `-2` is load-bearing: at the default wave the policy is applied after the
`Cluster` at `-1`, which cannot go Healthy until the `cnpg-system` rule exists,
so the sync parks at `-1` forever. Drop the `ingress` rule if the Gateway does
not reach this workload directly (a namespace fronted by Authentik's outpost
takes its traffic from the `authentik` namespace). A Prometheus rule belongs
here only with a ServiceMonitor, on that port, with `rules.http`; the shape
and the rollout are in
[Security Policies](../platform/security-policies.md#network-policies).

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

DNS and TLS need nothing further — see [Gateway API](../platform/gateway-api.md).

### Putting a workload behind Authentik

- **It speaks OIDC:** the `oauth2provider` blueprint lives in the workload's
    directory; its client credentials come from `scripts/bao-secrets.sh` under
    `kv/<app>/config` plus one projected-volume source in
    `payload/platform/authentik/application.yaml`; the discovery URI is
    `auth.k8s.wlkr.ch`.
- **It does not:** add a `proxyprovider` on the embedded outpost, point the
    `HTTPRoute` at `authentik-server` in the `authentik` namespace, and add the
    namespace to `payload/platform/authentik/referencegrant.yaml`.

Either way it is two pull requests, one here, because a workload must not be
able to put itself behind or take itself out from behind the cluster's
authentication layer. Details in [Authentik](../platform/authentik.md).

## Step 4: Commit and push

```bash
git add my-app/
git commit -m "feat: add my-app"
git push
```

The ApplicationSet picks the directory up within about three minutes, or
immediately after **Refresh** on the `apps` ApplicationSet in the ArgoCD UI.

## Step 5: Document it

The page goes in the workloads repository at `docs/my-app.md`, registered in
its `zensical.toml`, which publishes to
[homelab-apps.wlkr.ch](https://homelab-apps.wlkr.ch/).
Both sites build with `--strict`, so an unregistered page fails CI. Say what
the application is for, which hostname it answers on, what it stores and
where, and what surprised you while deploying it.

## Troubleshooting

| Symptom | Cause and check |
| --- | --- |
| The Application was never generated | Only `*/application.yaml`, one level deep, is read, and a `spec.project` other than `apps` fails the whole set naming the file. Read the controller log (below) |
| The Application exists but will not sync | It has exhausted its retries, on a fresh cluster usually against a platform resource that arrived later than the retry budget. `argocd app sync <app>` — see [Sync policy](../architecture/gitops.md#sync-policy) |
| `Synced`, health `Unknown`, workload running fine | A wave is waiting on a resource that will never go Healthy; with a database it is the missing `cnpg-system` rule (`kubectl get cluster` says `1/1`, status reads `Instance Status Extraction Error: HTTP communication issue`). Confirm with `kubectl -n argocd get application my-app -o jsonpath='{.status.operationState.message}'` and the Hubble check below |
| Rule is in Git, still stuck | The policy's sync wave is wrong: the applied list has no `CiliumNetworkPolicy` while `Cluster` reads `Running`. Fix the wave, then `argocd app terminate-op my-app`; a stuck operation does not pick up a new revision |
| Pods will not start; events mention a security policy | `enforce` in `namespace.yaml` is stricter than the image needs. Loosen `enforce`, keep `audit` and `warn`, so violations stay in the [audit log](../architecture/audit-logging.md) — see [Pod Security Admission](../platform/security-policies.md#pod-security-admission) |
| Login redirects in a loop, or every link is `http://` | Requests arrive from Cilium's Envoy inside the pod CIDR (`10.244.0.0/16`) with TLS terminated at the Gateway. Set the application's trusted-proxy and "overwrite protocol" settings |

```bash
# Why an Application was not generated
kubectl -n argocd logs deploy/argocd-applicationset-controller | tail -50

# Drops from the operator, on the node running the database
kubectl -n kube-system exec <cilium-pod-on-that-node> -c cilium-agent -- \
  hubble observe --verdict DROPPED --from-namespace cnpg-system --last 20

# What the stuck sync has applied so far
kubectl -n argocd get application my-app -o jsonpath=\
'{range .status.operationState.syncResult.resources[*]}{.kind}{"\t"}{.hookPhase}{"\n"}{end}'
```
