---
description: "ArgoCD's chart values, its OIDC login through Authentik, and the break-glass path when that login is unavailable."
---

# ArgoCD

ArgoCD is the delivery layer: everything in `payload/` and in the workloads
repository reaches the cluster through it. How the Applications are generated
and staged is in [GitOps Strategy](../architecture/gitops.md); this page covers
the chart values and the supporting resources around ArgoCD itself.

## At a glance

| | |
| --- | --- |
| Namespace | `argocd` |
| Stage | `argocd` itself is applied by hand at bootstrap and is not staged; `argocd-projects` is `00-projects`, `argocd-config` is `08-services` |
| Depends on | [OpenBao](openbao.md) for the OIDC client secret, [Gateway API](gateway-api.md) for its route, [Authentik](authentik.md) for every login |
| If it is down | Nothing syncs. Running workloads are unaffected |
| Files | `payload/argocd/` (Application, `platform` ApplicationSet, `values.yaml`), `payload/platform/argocd-config/` (HTTPRoute, OIDC `ExternalSecret`, Grafana dashboard), `payload/platform/argocd-projects/` (AppProjects) |

## Configuration

The parts of `payload/argocd/values.yaml` that are not self-explanatory:

| Setting | Why |
| --- | --- |
| `redis-ha.haproxy` `maxSurge: 0` | Three replicas with hard per-host anti-affinity on three schedulable nodes. The default surges a fourth pod with nowhere to land and wedges every rollout; retiring first frees the node |
| `redis-ha.image.tag`, `redis-ha.haproxy.image.tag` | Both run ahead of the chart, whose exact patch tags stop being rebuilt once the next one lands and so miss base-image security fixes. Renovate carries them forward; each bare `tag:` must be listed under the `pinDigests: false` rule in `renovate.json` |
| Memory limits, no CPU limits | A CPU limit throttles even on an idle node; memory is not compressible. Sizing is in [Platform](index.md) |
| `controller` has no resources | The application-controller grows with the number of managed resources and is not yet sized |
| `metrics.enabled` on four components | Creates the `<component>-metrics` Services whose names are the `job` label the vendored dashboard filters on. The ServiceMonitors render only once the Prometheus operator CRDs exist, so `make install-argo` still works first |
| `server.extraArgs: --insecure` | TLS terminates at the Gateway |
| `admin.enabled: "false"` | With SSO in front, a shared admin password would bypass it with no audit trail |
| `resource.customizations.health.argoproj.io_Application` | Restores health assessment for `Application` resources, dropped in ArgoCD 1.8, so `argocd` reports the health of the ApplicationSet rather than a permanent Healthy |
| `applicationsetcontroller.enable.progressive.syncs` | Without it a `RollingSync` strategy is ignored and every Application syncs at once |
| `policy.default: ""` | An authenticated user with no matching Authentik group gets no access, not read-only-everything |

The OIDC client ID and secret come from `kv/authentik/config`, the same OpenBao
path Authentik's blueprint reads. `argocd-cm` refers to them as
`$argocd-oidc:client-id`; ArgoCD resolves a `$name:key` reference only against
a Secret labelled `app.kubernetes.io/part-of: argocd`, which the
`ExternalSecret` template sets.

The Grafana dashboard in `argocd-dashboard.yaml` is upstream's
`examples/dashboard.json`, unmodified, at the ArgoCD version the chart deploys.
Renovate does not see it: re-copy it when ArgoCD moves a minor version.

## Health check

```bash
kubectl -n argocd get applications
```

Every Application should be `Synced` and `Healthy`. Where a platform rollout is
stuck, the [ApplicationSet status](../architecture/gitops.md#what-the-staging-costs)
names the Application it is waiting for.

## Pitfalls

!!! warning "The local admin is disabled; Authentik is the login"
    With Authentik down there is no way into the UI. The break-glass path is to re-enable `admin.enabled` and read the initial password, as in [Quickstart step 10](../quickstart.md#setup) — see [When Authentik is down](authentik.md#when-authentik-is-down).

ArgoCD manages ArgoCD, and the ApplicationSet that deploys the cluster sits
inside that same Application. When a broken ArgoCD config has been synced by
ArgoCD, `make install-argo` reinstalls the chart from the deployment host, and
`kubectl apply -f payload/argocd/applicationset-platform.yaml` re-applies the
rollout itself.
