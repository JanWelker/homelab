---
description: "Authentik as the single sign-on layer: OIDC for ArgoCD and Grafana, a proxy outpost for the dashboards that have no login of their own."
---

# Authentik

[Authentik](https://goauthentik.io/) is the cluster's identity provider. Every
platform UI sits behind one login.

The alternative — the state most homelabs live in — is six dashboards with six
different credentials, three of which are the chart default, one of which is
written on a sticky note, and one of which has no authentication at all because
the project never shipped any.

| Service | Authentication |
| --- | --- |
| ArgoCD | OIDC, local admin **disabled** |
| Grafana | OIDC, login form disabled |
| Nextcloud | OIDC, login form hidden, local admin break-glass |
| Home Assistant | Proxy outpost **in front of** its own login — see [the exception](#home-assistant-is-the-exception) |
| Rook dashboard | Proxy outpost |
| Hubble UI | Proxy outpost |
| Prometheus | Proxy outpost |
| Alertmanager | Proxy outpost |

Left to themselves these each carry a different answer — a local `admin` account
with an API key, a chart-generated password, separate Ceph credentials — and
Hubble UI carries none at all, which would publish cluster-wide network flow
data to anyone who could reach the hostname. Hubble is the one that should make
you sit up: it is a live map of every connection in the cluster, served without
so much as a password prompt.

## At a glance

| | |
| --- | --- |
| Namespace | `authentik` |
| Stage | `08-services` |
| Depends on | [OpenBao](openbao.md) for its OIDC client secrets and database password, [Rook-Ceph](rook-ceph.md) for Postgres |
| If it is down | Every platform UI. ArgoCD, Grafana, Hubble, Prometheus, Alertmanager and the Rook dashboard all lose their only login — see [When Authentik is down](#when-authentik-is-down) |
| Health check | `kubectl -n authentik get pods` &rarr; server, worker and Postgres all Ready |
| UI | `auth.infra.k8s.wlkr.ch` |

## Two integration styles

**OIDC**, for applications that can do it themselves. ArgoCD and Grafana each
talk to Authentik directly and map group membership to a role.

**Proxy**, for the five that cannot. Authentik's embedded outpost is a reverse
proxy: the hostname resolves to the outpost, the outpost authenticates the
request, and only then forwards it to the real backend. No support is needed
from the application, which is the only option for something like Hubble. It is
the classic auth-in-front-of-a-dumb-backend pattern, and it works precisely as
well as your certainty that nothing else can reach the backend directly.

```mermaid
flowchart LR
    U([Browser]) --> GW[infra-gateway]
    GW -->|argo, monitoring| APP[ArgoCD / Grafana]
    APP -.->|OIDC redirect| AK[Authentik]
    GW -->|hubble, rook, prometheus, alertmanager| AK
    AK -->|authenticated| BE[Hubble UI / Ceph dashboard / ...]
```

### Home Assistant is the exception

Every other proxied application has no login of its own, which is what makes
the outpost *the* authentication rather than an extra one. Home Assistant does
have a login, and upstream ships no OIDC provider to replace it — the only auth
providers in the codebase are `homeassistant`, `command_line`,
`trusted_networks` and an example marked insecure.

So the outpost sits *in front of* Home Assistant's own login rather than
instead of it. Browser users authenticate twice. That is defence in depth, not
single sign-on, and it is worth being honest about which one you are getting.

The companion apps and webhooks cannot complete an interactive Authentik login
at all — they hold a long-lived token — so `skip_path_regex` on the provider
lets `/api/`, `/auth/token` and the external-auth callback through untouched.
**Home Assistant's own accounts still guard those paths**, and they are the
only thing guarding them. Revoking someone's Authentik account does not revoke
their Home Assistant token; that has to be done in Home Assistant.

## Two hostnames

Authentik answers on two names, and which one a client uses is not arbitrary:

| Hostname | Gateway | Reachable from | Used by |
| --- | --- | --- | --- |
| `auth.infra.k8s.wlkr.ch` | `infra-gateway` | the local network | ArgoCD, Grafana, break-glass `akadmin` login |
| `auth.k8s.wlkr.ch` | `apps-gateway` | outside it too | Nextcloud |

The workload hostnames are reachable from outside the local network and the
`*.infra` ones are not, so an OIDC client that users reach from outside would
send them to an authorize endpoint that does not resolve, and the login would
hang with no useful error.

Authentik needs no configuration for this. Every URL it publishes — issuer,
authorize, token, userinfo, jwks — is built with `request.build_absolute_uri()`,
so it serves a self-consistent OpenID configuration on whichever name the
request arrived on.

!!! warning "A client must never mix the two"
    The `iss` claim in the token is the hostname the token was issued through, and a client compares it against the issuer it discovered. Point a client's discovery URI at one name and its redirect at the other and every login fails the issuer check — with an error that blames the token, not the hostname. Each client is pinned to exactly one name; ArgoCD and Grafana are on the infra name, and nothing about them changed when the second route was added.

Proxied applications are unaffected either way: their route points at
`authentik-server`, so the whole flow — including the login — happens on the
application's own hostname and never redirects to either of these.

### Where the proxied routes point

The `HTTPRoute` for `hubble.infra.k8s.wlkr.ch` stays in `payload/platform/cilium/`
next to the thing it exposes, but its `backendRef` is `authentik-server` in the
`authentik` namespace. Gateway API forbids a cross-namespace `backendRef` unless
the target namespace grants it, so `referencegrant.yaml` allows exactly that:
`HTTPRoute` objects, from `kube-system`, `rook-ceph` and `home-assistant`
only, to the `authentik-server` Service only.

`home-assistant` is on that list because its route lives in the
[workloads repository](../development/add-workload.md) — the one place a
workload needs something granted to it here. Adding a proxied workload is
therefore two pull requests, which is the intended friction: a workload should
not be able to put itself behind, or take itself out from behind, the
authentication layer on its own.

Prometheus and Alertmanager have no route of their own to reuse, so theirs live
in the `authentik` directory. **Neither has authentication of its own** —
publishing them at all is only defensible because the outpost authenticates in
front of them.

## Where a blueprint lives

Platform providers are in `blueprints.yaml` here. A **workload's** provider is
not: it lives in that workload's own directory in the
[workloads repository](../development/add-workload.md), as a ConfigMap targeted
at this namespace, so the provider and the application it authenticates change
in one commit.

This repository keeps the parts that cannot safely be delegated:

| Stays here | Why |
| --- | --- |
| The client credentials, in `bao-secrets.sh` | A workload cannot mint its own, which is what keeps SSO onboarding a deliberate, two-repository act |
| The `secret-generation` annotation | Adding a credential means restarting the worker that reads it — see the warning below |
| The mount entry, in `application.yaml` | One projected-volume source per workload |
| The embedded outpost's `providers:` list | It replaces a single global object; two repositories writing it would overwrite each other |
| `referencegrant.yaml` | A proxied workload's route needs granting from this side |

!!! warning "The mount must be optional, and not `blueprints.configMaps`"
    `blueprints.configMaps` renders a plain `configMap` volume, and the kubelet refuses to start a pod whose ConfigMap does not exist. Workload blueprints arrive in `12-workloads`, four stages after Authentik has to be Healthy — so a required mount means the worker waits for a stage that is waiting for the worker. The workload sources are a `projected` volume instead, which accepts `optional: true`, and the worker starts whether or not any of them exist yet.

`blueprints_discovery` runs on the worker's startup, hourly, and on a file
watcher, so a blueprint that lands later is picked up without a restart —
asynchronously, which is why anything configuring itself *against* a provider
has to tolerate it not being there yet.

!!! danger "A new client credential needs the worker restarted"
    `envFrom` injects `authentik-secrets` as environment variables **once, when the pod starts** — External Secrets updating that Secret afterwards changes nothing a running worker can see, so a blueprint reading a newly added credential through `!Env` gets an empty string and creates a provider that exists but does not work, answering 404 on its discovery endpoint. Nextcloud's first rollout lost this race by two seconds: the worker started at `14:34:10` and `authentik-secrets` gained `NEXTCLOUD_CLIENT_ID` at `14:34:12`. So `worker.podAnnotations.homelab.wlkr.ch/secret-generation` in `application.yaml` is **bumped whenever `authentik-secrets` gains a key** — that changes the pod template, and ArgoCD restarts the worker in the same sync that adds the credential. A `kubectl rollout restart` fixes a running cluster but leaves nothing behind for the next person.

## Configuration as code

Providers and applications are declared in blueprints
(`blueprints.yaml`), mounted into the worker as a ConfigMap. Clicking them
together in the UI would mean losing them on the next rebuild — and identity
configuration is exactly the sort of thing you set up once, forget entirely, and
then cannot reconstruct under pressure two years later.

Three tags do the work:

- `!Find` resolves objects Authentik ships by default, such as the default
  authorization flow.
- `!KeyOf` references another entry in the same blueprint by its `id`.
- `!Env` reads an environment variable, which is how client secrets get in
  without being written to Git.

The worker discovers every `.yaml` key in the ConfigMap and applies it.

!!! warning "OAuth2 providers must name their grant types"
    Authentik 2026.x restricts which OAuth2 grants a provider serves, and
    `grant_types` defaults to an empty list. A provider that omits it serves
    none, and the authorize endpoint turns every login away with
    `invalid_request: The request is otherwise malformed`. The field looks
    optional; it is not.

!!! warning "The outpost entry replaces its provider list"
    The `authentik_outposts.outpost` entry sets `providers` wholesale rather than appending. Every proxied application must be listed there — adding a fifth and forgetting this line silently unassigns the other four, which means four dashboards quietly stop being protected rather than loudly breaking. Failing open is the worst failure mode a security control can have.

## Chart values

- **`authentik.web.base_url`.** Authentik cannot reliably infer the URL it is
  reached on, and builds e-mail links and outpost redirects from it. Unset,
  every admin page shows "The base URL has not been configured" and the worker
  logs the same on every reconcile. The UI stores it on the tenant in the
  database; setting it in the chart backfills it, so a rebuilt cluster needs no
  click.
- **`metrics.enabled` and `metrics.serviceMonitor.enabled`.** The first creates
  the metrics Service, and the chart renders the ServiceMonitor only when both
  are set. The ServiceMonitor switch alone produces neither object, silently.
  The worker is scraped too: tasks, outpost state and blueprint runs are
  measured there, not on the server.
- **Postgres resources.** Sized from a measured 281Mi peak. Postgres memory is
  bounded by `shared_buffers` and `work_mem` rather than by load, so it is
  steadier than the number suggests.

## Client secrets are generated up front

The OIDC client ID and secret are shared values: Authentik needs them, and so do
ArgoCD and Grafana. Rather than letting Authentik mint a secret that then exists
only in its database, both are generated once into OpenBao and read from there
by both sides — Authentik through `!Env`, the clients through their own
`ExternalSecret`.

That is what makes the whole thing reproducible: a rebuilt Authentik gets the
same client credentials, and nothing has to be copied out of a UI. Anything that
exists only inside a running system's database is not configuration, it is a
hostage situation.

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
`akadmin` with `bootstrap-password`, and create the groups below.

## Groups and roles

Authorisation is group membership. Create these in Authentik and add users:

| Group | Grants |
| --- | --- |
| `argocd-admins` | ArgoCD `role:admin` |
| `argocd-viewers` | ArgoCD `role:readonly` |
| `grafana-admins` | Grafana `Admin` |
| `grafana-editors` | Grafana `Editor` |

ArgoCD's `policy.default` is empty, so an authenticated user in neither ArgoCD
group gets **no** access rather than read-only-everything. That default is worth
copying elsewhere: "authenticated" and "authorised" are different questions, and
plenty of systems answer the second one with a shrug. Grafana falls back to
`Viewer`.

Access to a proxied application is controlled in Authentik itself, by binding
policies or groups to the application.

## When Authentik is down

Authentik becoming a dependency of every UI is the cost of this, and it is a real
one: single sign-on is also a single point of failure for logging in at all.
Read this section *before* you need it, because the ArgoCD escape hatch below
requires a working `kubectl`, and you will be reaching for it on the day nothing
else works. Both OIDC integrations keep a break-glass path:

**ArgoCD** — re-enable the local admin account:

```bash
kubectl -n argocd patch cm argocd-cm --type merge \
  -p '{"data":{"admin.enabled":"true"}}'
kubectl -n argocd rollout restart deploy/argocd-server
```

**Grafana** — the login form is hidden, not removed. The admin account still
works through the API, and setting `GF_AUTH_DISABLE_LOGIN_FORM=false` brings the
form back.

The four proxied dashboards have no bypass: with the outpost down, the hostname
does not answer, full stop. Reach them by port-forward instead — which is a
reminder that `kubectl port-forward` is the universal break-glass tool and the
reason keeping a working kubeconfig off-cluster matters:

```bash
kubectl -n kube-system port-forward svc/hubble-ui 8080:80
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

### ArgoCD's API key account

Disabling the local admin also removes the `accounts.admin: apiKey` capability
that was configured for MCP integration. If a token is still needed, add a
dedicated account rather than re-enabling admin — an account scoped to what the
token is for is easier to reason about and to revoke:

```yaml
configs:
  cm:
    accounts.mcp: apiKey
  rbac:
    policy.csv: |
      p, role:mcp, applications, get, */*, allow
      g, mcp, role:mcp
```

## Directory Structure

```text
authentik/
├── application.yaml       # ArgoCD Application (Helm: goauthentik/authentik)
├── secrets.yaml           # ExternalSecret: secret key, DB and OIDC clients
├── blueprints.yaml        # ConfigMap: providers, applications, outpost
├── httproute.yaml         # auth / prometheus / alertmanager hostnames
└── referencegrant.yaml    # Lets the hubble and rook routes reach the outpost
```
