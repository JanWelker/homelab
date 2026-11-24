# ArgoCD

ArgoCD is the delivery layer, [chosen over Flux](<https://homelab.wlkr.ch/architecture/decisions/index.md#argocd-with-an-applicationset-not-flux>): everything in `payload/` and in the workloads repository reaches the cluster through it. How the Applications are generated and synced is in [GitOps Strategy](<https://homelab.wlkr.ch/architecture/gitops/index.md>); this page covers the chart values and the supporting resources around ArgoCD itself.

## At a glance

|  |  |
| --- | --- |
| Namespace | `argocd` |
| Depends on | [OpenBao](<https://homelab.wlkr.ch/platform/openbao/index.md>) for the OIDC client secret, [Gateway API](<https://homelab.wlkr.ch/platform/gateway-api/index.md>) for its route, [Authentik](<https://homelab.wlkr.ch/platform/authentik/index.md>) for every login |
| If it is down | Nothing syncs. Running workloads are unaffected |
| Health check | `kubectl -n argocd get applications` → every one `Synced` and `Healthy` |
| Files | [`payload/argocd/`](<https://github.com/JanWelker/homelab/tree/main/payload/argocd>) (Application, `platform` ApplicationSet, `values.yaml`), [`payload/platform/argocd-config/`](<https://github.com/JanWelker/homelab/tree/main/payload/platform/argocd-config>) (OIDC `ExternalSecret`, Grafana dashboard), [`payload/platform/argocd-projects/`](<https://github.com/JanWelker/homelab/tree/main/payload/platform/argocd-projects>) (AppProjects) |

## Configuration

The parts of `payload/argocd/values.yaml` that are not self-explanatory:

| Setting | Why |
| --- | --- |
| `redis-ha.image.tag`, `redis-ha.haproxy.image.tag` | Both run ahead of the chart, whose exact patch tags stop being rebuilt once the next one lands and so miss base-image security fixes. Renovate carries them forward; each bare `tag:` must be listed under the `pinDigests: false` rule in `renovate.json` |
| Memory limits, no CPU limits | A CPU limit throttles even on an idle node; memory is not compressible. Sizing is in [Platform](<https://homelab.wlkr.ch/platform/index.md>) |
| `metrics.enabled` | Creates the `<component>-metrics` Services whose names are the `job` label the vendored dashboard filters on. The ServiceMonitors render only once the Prometheus operator CRDs exist, so `make install-argo` still works first |
| `configs.params` `server.insecure: true` | TLS terminates at the Gateway. The chart reads this parameter, not `extraArgs`, to point its route at the plaintext port |
| `server.httproute` | The chart renders the `HTTPRoute` to `argocd-server`, so it is not a file of its own; the response-header filters are the same every UI route carries — see [Gateway API](<https://homelab.wlkr.ch/platform/gateway-api/index.md#usage>) |
| `resource.customizations` for `HTTPRoute`, `GRPCRoute` and `PushSecret` | The API server defaults fields no manifest sets: a route's backend `group`, `kind`, `weight` and catch-all `matches`, a `PushSecret`'s `conversionStrategy`. Without the ignore list each object is permanently OutOfSync. Only the catch-all value is ignored, so a changed path still syncs |
| `resourceExclusionsAdditional` for the ARC runner CRs | The runner controller creates and deletes them per job; appended, so the chart's default exclusions stay too |
| `dex.enabled: false` | `oidc.config` talks to [Authentik](<https://homelab.wlkr.ch/platform/authentik/index.md>) directly, so the chart's bundled Dex would be a second login service with nothing behind it; the redirect URI is the server's own `/auth/callback` either way |
| `notifications.enabled: false` | Nothing is configured to notify, and the controller's ClusterRole reads every Secret in the cluster |
| `admin.enabled: "false"` | With SSO in front, a shared admin password would bypass it with no audit trail |
| `resource.customizations.health.argoproj.io_Application` | Restores health assessment for `Application` resources, dropped in ArgoCD 1.8, so `argocd` reports the health of the ApplicationSet rather than a permanent Healthy |
| `policy.default: ""` | An authenticated user with no matching Authentik group gets no access, not read-only-everything |
| `timeout.reconciliation` and the ApplicationSets' `requeueAfterSeconds` | ArgoCD listens on a private address, so GitHub's webhook cannot reach it and a commit waits for the next poll. The two run separately: the first refreshes Applications, the second re-reads the generator paths. The repo-server caches a revision for the first, so it must not exceed the second or a new commit's files stay invisible to the generator until the cache expires |

The OIDC client ID and secret come from `kv/authentik/config`, the same OpenBao path Authentik's blueprint reads. `argocd-cm` refers to them as `$argocd-oidc:client-id`; ArgoCD resolves a `$name:key` reference only against a Secret labelled `app.kubernetes.io/part-of: argocd`, which the `ExternalSecret` template sets.

The Grafana dashboard in `grafana-dashboards.yaml` is upstream's `examples/dashboard.json`, unmodified, at the ArgoCD version the chart deploys. Renovate does not see it: re-copy it when ArgoCD moves a minor version.

## Usage

### Projects

The `apps` project lists the repositories its Applications may pull from in `payload/platform/argocd-projects/projects.yaml`. An OCI Helm chart is listed and referenced **without** the `oci://` scheme (`ghcr.io/janwelker/charts` as `sourceRepos` entry and as the Application's `repoURL`, with `chart:` naming the chart): Argo CD compares the two strings after normalising, and treats a `repoURL` with no scheme as OCI, so a public registry needs no repository Secret and no `enableOCI`. The `default` project Argo CD creates on install is declared in the same file with empty lists: it cannot be deleted, so an Application that names no project lands there and must be able to do nothing.

### Agent account

The `claude` account is the in-cluster Claude Code agents' API identity. It may read everything `role:readonly` reads, `argocd app get --refresh` included, and nothing else: the sessions investigate and open pull requests, and every Application syncs itself. It has `apiKey` only, so it cannot log in. A session with `argocd.enabled` reads its own copy at `kv/claude-<session>/argocd`; only `homelab` has one.

The token has no expiry, so nothing breaks unattended and a leak lasts until rotation. To rotate:

1. Generate the token, logged in through Authentik:

   ```bash
   argocd account generate-token --account claude
   ```
2. Store it with `make bao-secrets`, at `kv/claude-homelab/argocd` (`token`).
3. [Force a sync](<https://homelab.wlkr.ch/platform/external-secrets/index.md#usage>) of the `claude-homelab-argocd` `ExternalSecret` in `claude-homelab`; the wrapper reads the mounted file on every call, so no pod restarts.
4. Revoke the previous token; `argocd account get --account claude` lists the IDs with their expiry (a token showing none predates this policy):

   ```bash
   argocd account delete-token --account claude <id>
   ```

The agents reach Argo CD on 443 through the CLI host from [CLI access](<#cli-access>), which resolves to `infra-gateway`'s own address. The caller's policy has to admit both legs, as for any name the Gateway serves ([Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#network-policies>)):

| Leg | Admitted by |
| --- | --- |
| Agent pod to the Gateway address | A name rule for the CLI host: the datapath classifies the Gateway's address as `world`, not `ingress` |
| The Gateway's Envoy to `argocd-server` | An egress rule to the `argocd-server` pod itself. Envoy checks the caller's egress policy again against the pod it picked and answers `403 Access denied` when no rule matches, audit mode or not. The server's own policy already admits the `ingress` entity |

The `homelab` session's policy in the workloads repository carries both. The chart's own policy has only the name rule ([claude-agent\#3](<https://github.com/JanWelker/claude-agent/issues/3>)), so any other session with `argocd.enabled` gets a 403 on every call.

### CLI access

The `argocd` CLI talks gRPC, which the browser route cannot carry: Cilium's Gateway translates gRPC-web into native gRPC, and native gRPC needs an HTTP/2 backend, which the plain `http` port is not. The chart therefore adds an `h2c` port (`http2`, 81) and a `GRPCRoute` on `argo-grpc.infra.k8s.wlkr.ch`, a hostname of its own so the browser route stays as it is.

The Gateway offers no ALPN, and gRPC clients refuse TLS without it, so the CLI needs ALPN enforcement off:

```bash
export GRPC_ENFORCE_ALPN_ENABLED=false
argocd login argo-grpc.infra.k8s.wlkr.ch --sso --grpc-web
```

## Health check

```bash
kubectl -n argocd get applications
```

An Application stuck `OutOfSync` has exhausted its [retries](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>) and waits for a hand sync; `ArgoCDApplicationOutOfSync` in [Monitoring](<https://homelab.wlkr.ch/platform/monitoring/index.md#alerting>) mails it.

## Pitfalls

> [!WARNING]
>
> **The local admin is disabled; Authentik is the login**
>
> With Authentik down there is no way into the UI. The break-glass path is a pull request that re-enables `admin.enabled`, since selfHeal reverts a `kubectl patch`; the steps are in [When Authentik is down](<https://homelab.wlkr.ch/platform/authentik/index.md#when-authentik-is-down>).

## Recovery

### Reinstalling the chart

ArgoCD manages ArgoCD, and the ApplicationSet that deploys the cluster sits inside that same Application. When ArgoCD has synced a config that breaks it, from the deployment host:

1. Reinstall the chart.

   ```bash
   make install-argo
   ```
2. Re-apply the ApplicationSet.

   ```bash
   kubectl apply -f payload/argocd/applicationset-platform.yaml
   ```
3. Check that the Applications come back.

   ```bash
   kubectl -n argocd get applications
   ```
