---
description: "ArgoCD's chart values, its OIDC login through Authentik, and the break-glass path when that login is unavailable."
---

# ArgoCD

ArgoCD is the delivery layer: everything in `payload/` and in the workloads
repository reaches the cluster through it. How the Applications are generated
and synced is in [GitOps Strategy](../architecture/gitops.md); this page covers
the chart values and the supporting resources around ArgoCD itself.

## At a glance

| | |
| --- | --- |
| Namespace | `argocd` |
| Depends on | [OpenBao](openbao.md) for the OIDC client secret, [Gateway API](gateway-api.md) for its route, [Authentik](authentik.md) for every login |
| If it is down | Nothing syncs. Running workloads are unaffected |
| Files | `payload/argocd/` (Application, `platform` ApplicationSet, `values.yaml`), `payload/platform/argocd-config/` (OIDC `ExternalSecret`, Grafana dashboard), `payload/platform/argocd-projects/` (AppProjects) |

## Configuration

The parts of `payload/argocd/values.yaml` that are not self-explanatory:

| Setting | Why |
| --- | --- |
| `redis-ha.haproxy` `maxSurge: 0` | Three replicas with hard per-host anti-affinity on three schedulable nodes. The default surges a fourth pod with nowhere to land and wedges every rollout; retiring first frees the node |
| `redis-ha.image.tag`, `redis-ha.haproxy.image.tag` | Both run ahead of the chart, whose exact patch tags stop being rebuilt once the next one lands and so miss base-image security fixes. Renovate carries them forward; each bare `tag:` must be listed under the `pinDigests: false` rule in `renovate.json` |
| Memory limits, no CPU limits | A CPU limit throttles even on an idle node; memory is not compressible. Sizing is in [Platform](index.md) |
| `metrics.enabled` on four components | Creates the `<component>-metrics` Services whose names are the `job` label the vendored dashboard filters on. The ServiceMonitors render only once the Prometheus operator CRDs exist, so `make install-argo` still works first |
| `configs.params` `server.insecure: true` | TLS terminates at the Gateway. The chart reads this parameter, not `extraArgs`, to point its route at the plaintext port |
| `server.httproute` | The chart renders the `HTTPRoute` to `argocd-server`, so it is not a file of its own; the response-header filters are the same every UI route carries — see [Gateway API](gateway-api.md#usage) |
| `resource.customizations` for `HTTPRoute` | Cilium defaults `group`, `kind`, `weight` and the empty `matches` onto every route; without the ignore list each one is permanently OutOfSync |
| `admin.enabled: "false"` | With SSO in front, a shared admin password would bypass it with no audit trail |
| `resource.customizations.health.argoproj.io_Application` | Restores health assessment for `Application` resources, dropped in ArgoCD 1.8, so `argocd` reports the health of the ApplicationSet rather than a permanent Healthy |
| `policy.default: ""` | An authenticated user with no matching Authentik group gets no access, not read-only-everything |
| `timeout.reconciliation` and the ApplicationSets' `requeueAfterSeconds`, both one minute | ArgoCD listens on a private address, so GitHub's webhook cannot reach it and a commit waits for the next poll. The two run separately: the first refreshes Applications, the second re-reads the generator paths. The repo-server caches a revision for the first, so it must not exceed the second or a new commit's files stay invisible to the generator until the cache expires |

The OIDC client ID and secret come from `kv/authentik/config`, the same OpenBao
path Authentik's blueprint reads. `argocd-cm` refers to them as
`$argocd-oidc:client-id`; ArgoCD resolves a `$name:key` reference only against
a Secret labelled `app.kubernetes.io/part-of: argocd`, which the
`ExternalSecret` template sets.

The Grafana dashboard in `grafana-dashboards.yaml` is upstream's
`examples/dashboard.json`, unmodified, at the ArgoCD version the chart deploys.
Renovate does not see it: re-copy it when ArgoCD moves a minor version.

## Projects

The `apps` project lists the repositories its Applications may pull from in
`payload/platform/argocd-projects/projects.yaml`. An OCI Helm chart is listed
and referenced **without** the `oci://` scheme (`ghcr.io/janwelker/charts` as
`sourceRepos` entry and as the Application's `repoURL`, with `chart:` naming
the chart): Argo CD compares the two strings after normalising, and treats a
`repoURL` with no scheme as OCI, so a public registry needs no repository
Secret and no `enableOCI`.

## Agent account

The `claude` account is the in-cluster Claude Code agents' API identity. It
may read everything `role:readonly` reads and sync Applications in the `apps`
project, and nothing else. It has `apiKey` only, so it cannot log in.

1. Generate the token, logged in through Authentik:

    ```bash
    argocd account generate-token --account claude
    ```

2. Store it with `make bao-secrets`, at `kv/claude-agents/argocd`
   (`token`).

The agents reach Argo CD on 443 by name, so the traffic enters through
`infra-gateway` and arrives at `argocd-server` as the `ingress` entity, which
its policy already admits. They use the CLI host from
[CLI access](#cli-access).

## CLI access

The `argocd` CLI talks gRPC, which the browser route cannot carry: Cilium's
Gateway translates gRPC-web into native gRPC, and native gRPC needs an HTTP/2
backend, which the plain `http` port is not. The chart therefore adds an `h2c`
port (`http2`, 81) and a `GRPCRoute` on `argo-grpc.infra.k8s.wlkr.ch`, a
hostname of its own so the browser route stays as it is.

The Gateway offers no ALPN, and gRPC clients refuse TLS without it, so the CLI
needs ALPN enforcement off:

```bash
export GRPC_ENFORCE_ALPN_ENABLED=false
argocd login argo-grpc.infra.k8s.wlkr.ch --sso --grpc-web
```

## Health check

```bash
kubectl -n argocd get applications
```

Every Application should be `Synced` and `Healthy`. One that stays `OutOfSync`
has exhausted its retries and waits for a hand sync — see
[Sync policy](../architecture/gitops.md#sync-policy).

## Pitfalls

!!! warning "The local admin is disabled; Authentik is the login"
    With Authentik down there is no way into the UI. The break-glass path is to re-enable `admin.enabled` and read the initial password, as in [Quickstart step 10](../quickstart.md#setup) — see [When Authentik is down](authentik.md#when-authentik-is-down).

ArgoCD manages ArgoCD, and the ApplicationSet that deploys the cluster sits
inside that same Application. When a broken ArgoCD config has been synced by
ArgoCD, `make install-argo` reinstalls the chart from the deployment host, and
`kubectl apply -f payload/argocd/applicationset-platform.yaml` re-applies the
rollout itself.
