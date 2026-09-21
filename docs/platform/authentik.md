---
description: "Authentik as the single sign-on layer: OIDC for ArgoCD and Grafana, a proxy outpost for the dashboards that have no login of their own."
---

# Authentik

[Authentik](https://goauthentik.io/) is the cluster's identity provider. Every
platform UI sits behind one login instead of a chart-default password each —
and Hubble UI, which ships no authentication at all, would otherwise publish
every network flow in the cluster to anyone who could reach the hostname.

| Service | Authentication |
| --- | --- |
| ArgoCD | OIDC, local admin **disabled** |
| Grafana | OIDC, login form disabled |
| Nextcloud | OIDC, login form hidden, local admin break-glass |
| Home Assistant | Proxy outpost **in front of** its own login — see [Pitfalls](#pitfalls) |
| Rook dashboard, Hubble UI, Prometheus, Alertmanager | Proxy outpost |

## At a glance

| | |
| --- | --- |
| Namespace | `authentik` |
| Stage | `08-services` |
| Depends on | [OpenBao](openbao.md) for its OIDC client secrets and database password, [Rook-Ceph](rook-ceph.md) for Postgres |
| If it is down | Every platform UI loses its only login — see [When Authentik is down](#when-authentik-is-down) |
| Health check | `kubectl -n authentik get pods` &rarr; server, worker and Postgres all Ready |
| UI | `auth.infra.k8s.wlkr.ch` |
| Files | `payload/platform/authentik/` |

## Configuration

### Two integration styles

**OIDC**, for applications that can do it themselves: ArgoCD and Grafana map
group membership to a role. **Proxy**, for the rest: the hostname resolves to
Authentik's embedded outpost, a reverse proxy that authenticates the request
and only then forwards it to the backend. It needs nothing from the
application and protects exactly as far as nothing else can reach the backend.

```mermaid
flowchart LR
    U([Browser]) --> GW[infra-gateway]
    GW -->|argo, monitoring| APP[ArgoCD / Grafana]
    APP -.->|OIDC redirect| AK[Authentik]
    GW -->|hubble, rook, prometheus, alertmanager| AK
    AK -->|authenticated| BE[Hubble UI / Ceph dashboard / ...]
```

### Two hostnames

| Hostname | Gateway | Reachable from | Used by |
| --- | --- | --- | --- |
| `auth.infra.k8s.wlkr.ch` | `infra-gateway` | the local network | ArgoCD, Grafana, break-glass `akadmin` login |
| `auth.k8s.wlkr.ch` | `apps-gateway` | outside it too | Nextcloud |

An OIDC client used from outside the local network needs an authorize
endpoint that resolves there. Authentik builds every published URL from the
request, so each name serves a self-consistent OpenID configuration with no
configuration. Proxied applications never redirect to either: their route
points at `authentik-server`, so the whole flow happens on their own hostname.

!!! warning "A client must never mix the two"
    The `iss` claim is the hostname the token was issued through. A discovery URI on one name and a redirect on the other fails every login with an error that blames the token. Pin each client to exactly one name.

### Where the proxied routes point

Hubble's route stays in `payload/platform/cilium/`, Rook's in
`payload/platform/rook-ceph/` and Home Assistant's in the
[workloads repository](../development/add-workload.md), each with
`authentik-server` as `backendRef`. Gateway API forbids a cross-namespace
`backendRef` unless the target namespace grants it, so `referencegrant.yaml`
allows `HTTPRoute` objects from exactly those namespaces to that Service only.
A proxied workload is therefore two pull requests, by design: a workload
cannot take itself out from behind the authentication layer on its own.
Prometheus and Alertmanager have no route of their own, so theirs are in
`httproute.yaml` here; the outpost is their only authentication.

### Chart values

| Setting | Why |
| --- | --- |
| `authentik.web.base_url` | Authentik builds e-mail links and outpost redirects from it and cannot infer it; unset, every admin page shows "The base URL has not been configured". The chart value backfills the tenant, so a rebuilt cluster needs no click |
| `metrics.enabled` and `metrics.serviceMonitor.enabled` | The ServiceMonitor renders only when both are set; the switch alone produces nothing, silently. The worker is scraped too: tasks, outpost state and blueprint runs are measured there |
| `postgresql.image.tag` with its `# renovate:` annotation | The chart hardcodes a Debian 12 tag nothing tracks. The annotation lets Renovate move it (`versioning=docker`, so the `-trixie` suffix is a constraint, not a prerelease); an `allowedVersions` rule holds the major, because a Postgres major is a dump and restore — see [Renovate](../development/maintenance.md) |
| `worker.podAnnotations` `homelab.wlkr.ch/secret-generation` | Bumped whenever `authentik-secrets` or `authentik-secrets-nextcloud` gains a key, so ArgoCD restarts the worker in the same sync — see [Pitfalls](#pitfalls) |
| Workload blueprints as a `projected` volume, `optional: true` | `blueprints.configMaps` renders a plain `configMap` volume, and the kubelet refuses a pod whose ConfigMap is missing. Workload blueprints arrive in `12-workloads`, four stages after Authentik must be Healthy, so a required mount [deadlocks the rollout](../architecture/gitops.md) |

### Where a blueprint lives

Providers and applications are blueprints, so a rebuild reproduces them.
`!Find` resolves objects Authentik ships, `!KeyOf` references another entry in
the same blueprint, and `!Env` reads an environment variable, which keeps
client secrets out of Git. `blueprints_discovery` runs on worker startup,
hourly and on a file watcher, asynchronously, so anything configuring itself
against a provider must tolerate it not existing yet.

| What | Lives in | Why there |
| --- | --- | --- |
| Platform providers, applications, the outpost | `blueprints.yaml` | Mounted into the worker as a ConfigMap |
| A workload's provider and application | The workload's directory in the workloads repository, as a ConfigMap targeted at `authentik` | The provider and the application it authenticates change in one commit |
| Client credentials | `bao-secrets.sh`, read by `secrets.yaml` | A workload cannot mint its own; SSO onboarding stays a deliberate two-repository act |
| The mount entry and `secret-generation` | `application.yaml` | One projected-volume source per workload; a new credential needs the worker restarted |
| The outpost's `providers:` list | `blueprints.yaml` | It replaces one global object; two repositories writing it would overwrite each other |
| `referencegrant.yaml` | Here | A proxied workload's route needs granting from this side |

### Groups and roles

Authorisation is group membership. Create these in Authentik and add users:

| Group | Grants |
| --- | --- |
| `argocd-admins` | ArgoCD `role:admin` |
| `argocd-viewers` | ArgoCD `role:readonly` |
| `grafana-admins` | Grafana `Admin` |
| `grafana-editors` | Grafana `Editor` |

ArgoCD's `policy.default` is empty, so a user in neither ArgoCD group gets
**no** access; Grafana falls back to `Viewer`. Access to a proxied application
is bound to the application in Authentik itself.

## Usage

### Client secrets

The OIDC client IDs and secrets are generated once into OpenBao and read by
both sides — Authentik through `!Env`, ArgoCD and Grafana through their own
`ExternalSecret` — so a rebuilt Authentik gets the same credentials and
nothing is copied out of a UI. `make bao-secrets` does this; by hand:

```bash
bao kv put kv/authentik/config \
  secret-key="$(openssl rand -base64 60 | tr -d '\n')" \
  postgres-password="$(openssl rand -base64 32 | tr -d '\n')" \
  bootstrap-password="$(openssl rand -base64 24 | tr -d '\n')" \
  bootstrap-token="$(openssl rand -hex 32)" \
  argocd-client-id="$(openssl rand -hex 16)" \
  argocd-client-secret="$(openssl rand -base64 48 | tr -d '\n')" \
  grafana-client-id="$(openssl rand -hex 16)" \
  grafana-client-secret="$(openssl rand -base64 48 | tr -d '\n')"
```

Then log in at [auth.infra.k8s.wlkr.ch](https://auth.infra.k8s.wlkr.ch) as
`akadmin` with `bootstrap-password` and create the groups above.

### Adding an application

1. Generate its client credentials into OpenBao and add them to
   `bao-secrets.sh` and an `ExternalSecret` (a workload's own, mounted
   `optional`, like `secrets-nextcloud.yaml`).
2. Bump `homelab.wlkr.ch/secret-generation` in `application.yaml`.
3. Add the provider and application blueprint: an OAuth2 provider with
   `grant_types`, or a proxy provider added to the outpost's `providers:` list.
4. For a proxied application, point its `HTTPRoute` at `authentik-server` and
   add its namespace to `referencegrant.yaml`.
5. Bind the groups or policies that may reach it, in Authentik.

## Pitfalls

!!! danger "A new client credential needs the worker restarted"
    `envFrom` injects `authentik-secrets` **once, when the pod starts**. A blueprint reading a newly added credential through `!Env` on a running worker gets an empty string and creates a provider that answers 404 on its discovery endpoint. Bumping `secret-generation` restarts the worker in the sync that adds the credential; `kubectl rollout restart` fixes a running cluster but leaves nothing for the next one.

!!! warning "OAuth2 providers must name their grant types"
    `grant_types` defaults to an empty list. A provider that omits it serves no grants, and every login fails with `invalid_request: The request is otherwise malformed`.

!!! warning "The outpost entry replaces its provider list"
    `authentik_outposts.outpost` sets `providers` wholesale. Every proxied application must be listed there; adding one and forgetting the list silently unassigns the others, which fail open.

!!! note "Home Assistant is the exception"
    It has a login of its own and upstream ships no OIDC provider to replace it, so the outpost sits *in front of* it: browser users authenticate twice, which is defence in depth rather than single sign-on. The companion apps and webhooks hold a long-lived token and cannot complete an interactive login, so `skip_path_regex` lets `/api/`, `/auth/token` and the external-auth callback through. Home Assistant's own accounts are the only thing guarding those paths; revoking an Authentik account does not revoke a Home Assistant token.

## Recovery

### When Authentik is down

Single sign-on is a single point of failure for logging in at all. The ArgoCD
path needs a working `kubectl`, so keep a kubeconfig off-cluster.

**ArgoCD** — re-enable the local admin account:

```bash
kubectl -n argocd patch cm argocd-cm --type merge \
  -p '{"data":{"admin.enabled":"true"}}'
kubectl -n argocd rollout restart deploy/argocd-server
```

**Grafana** — the login form is hidden, not removed: the admin account works
through the API, and `GF_AUTH_DISABLE_LOGIN_FORM=false` brings the form back.

**Proxied dashboards** have no bypass; port-forward instead:

```bash
kubectl -n kube-system port-forward svc/hubble-ui 8080:80
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

### ArgoCD's API key account

Disabling the local admin also removes its `apiKey` capability. If a token is
needed, add an account scoped to what the token is for rather than
re-enabling admin:

```yaml
configs:
  cm:
    accounts.mcp: apiKey
  rbac:
    policy.csv: |
      p, role:mcp, applications, get, */*, allow
      g, mcp, role:mcp
```
