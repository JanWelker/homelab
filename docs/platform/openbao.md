---
description: "OpenBao as the cluster secret store: bootstrapping, unsealing, the Kubernetes auth method, and backups."
---

# OpenBao

[OpenBao](https://openbao.org/) is the cluster's secret store, an open-source
fork of HashiCorp Vault. It holds every secret the cluster consumes, surfaced
as native `Secret` objects by the [External Secrets Operator](external-secrets.md).
Sealed, nothing that needs a credential works, and the failure looks like six
unrelated things breaking at once.

```mermaid
flowchart LR
    Operator([Operator]) -->|bao CLI| Bao[(OpenBao<br/>KV v2)]
    ESO[External Secrets<br/>Operator] -->|read| Bao
    ESO -->|create/update| KSecret[K8s Secret]
    App[App Pod] -->|env / volume| KSecret

    style Bao fill:#e1f5ff,stroke:#0288d1
```

## At a glance

| | |
| --- | --- |
| Namespace | `openbao` |
| Depends on | [Rook-Ceph](rook-ceph.md) for its Raft volumes. A fresh bootstrap [pauses here](../architecture/gitops.md#bootstrap-pauses-at-openbao) until OpenBao is initialised and unsealed |
| If it is down — or merely sealed | No `ExternalSecret` resolves, so cert-manager cannot renew and pods that mount a materialised Secret will not start. It looks healthy from the outside |
| Health check | `kubectl -n openbao exec openbao-0 -- bao status` &rarr; `Sealed: false` on all three |
| UI | `vault.infra.k8s.wlkr.ch` — the one platform UI *not* behind Authentik, deliberately |
| Files | `payload/platform/openbao/` |

## Configuration

| Property | Value |
| --- | --- |
| Mode | HA, integrated Raft on a Ceph PVC per replica; the replica count is in `application.yaml` |
| Audit storage | Separate PVC on `rook-ceph-block`, written by the `file` audit device — see [Audit devices](#audit-devices) |
| TLS | Terminates at the Gateway; plain HTTP at `http://openbao.openbao.svc.cluster.local:8200` |
| Seal | Shamir, 5 shares, threshold 3, unsealed by hand |
| Chart | Upstream [`openbao/openbao-helm`](https://github.com/openbao/openbao-helm), pinned in `application.yaml` |

| Setting | Why |
| --- | --- |
| Pod security context restated in full | OpenBao keeps the root key out of swap with `mlock`, which needs `IPC_LOCK` — the reason the namespace enforces `privileged`; the container drops `ALL` and adds only that. The chart's `securityContext.pod` replaces rather than merges, and the image's `USER` is a name, so dropping the numeric IDs makes the kubelet refuse the container as unprovably non-root — on pod recreation, typically after a reboot |
| `image.repository` without a registry | The chart prepends `server.image.registry` (`quay.io`) itself |
| `unauthenticated_metrics_access` | `/v1/sys/metrics` otherwise wants a token the ServiceMonitor does not have. The metrics carry counts and timings, not paths or secrets, and only the namespace policy's callers and the Gateway reach port 8200 |
| `retry_join` stanzas | `bao operator init` initialises one Raft cluster on one pod; these make the other replicas join it as they start. `service_registration "kubernetes"` only labels pods `active` and `standby` |
| `serverTelemetry.grafanaDashboard` | Renders OpenBao's upstream dashboard (grafana.com 23725) |

### Audit devices

Two, declared as `audit` stanzas in the server config in `application.yaml`
(this OpenBao refuses `bao audit enable` over the API): a `file` device on the
audit PVC, and a second `file` device on `stdout`, which [Alloy](logging.md)
ships to Loki with the rest of the container's output. Without one, who read
which secret is recorded nowhere; with only one, a full PVC would make OpenBao
refuse every request, because a request no enabled device can log is refused.
Values are HMACed in both; paths and identities are not. The config is read at
start-up, and the StatefulSet updates `OnDelete`, so a change lands when each
pod next restarts — see [Unsealing after a restart](#unsealing-after-a-restart).

`OpenBaoSecretReadOutsideEso` in [`loki-rules.yaml`](logging.md#alerting)
fires on a `kv/data/` read by anything but the External Secrets Operator's
policy: a person with the root token, or a token that should not exist.

```logql
{namespace="openbao", container="openbao"} |= `"type":"response"` |= `kv/data/` | json | __error__=""
```

### KV layout

One KV v2 engine at `kv/`; every leaf is `<workload>/<purpose>`, and
ExternalSecrets reference `cert-manager/route53` without the `data/` prefix ESO
adds itself. The `bao kv put` for each path sits in a comment at the top of the
`ExternalSecret` that consumes it, collected in [Quickstart step 11](../quickstart.md).

| Path | Keys | Read by |
| --- | --- | --- |
| `authentik/config` | `secret-key`, `bootstrap-password`, `bootstrap-token`, the ArgoCD and Grafana client id/secret pairs | Authentik, ArgoCD and Grafana — generating the OIDC credentials up front keeps both sides of each integration declarative |
| `cert-manager/route53` | `access-key-id`, `secret-access-key` | The `certificates` Application |
| `external-dns/route53` | `access-key-id`, `secret-access-key` | external-dns |
| `monitoring/grafana-admin` | `password` | Grafana |
| `monitoring/smtp` | `username`, `password`, `to` | Alertmanager |
| `nextcloud/config` | `username`, `password`, `oidc-client-id`, `oidc-client-secret` | Nextcloud, and Authentik for the two `oidc-*` keys — a separate path so rotating it cannot take Authentik's own credentials with it |

The two `route53` leaves are separate IAM users on purpose: cert-manager's
key only writes `_acme-challenge` TXT records, so stolen it can issue
certificates, while external-dns's creates and deletes A records, so stolen it
can repoint hostnames. One shared key collapses both into "someone owns your
domain".

### Kubernetes auth

ESO authenticates with a short-lived ServiceAccount JWT, which OpenBao
validates against the TokenReview API (`rbac.yaml` binds
`system:auth-delegator` to the `openbao` ServiceAccount) and answers with a
token bound to the `external-secrets` policy. The `external-secrets-vault`
ServiceAccount and the `ClusterSecretStore` live in `cluster-secret-store.yaml`
here, because the store only validates against a running, unsealed OpenBao.
`make bao-init` configures all of it:

```bash
bao secrets enable -path=kv -version=2 kv
bao auth enable kubernetes
bao write auth/kubernetes/config kubernetes_host="https://kubernetes.default.svc"
bao policy write external-secrets - <<'EOF'
path "kv/data/*"     { capabilities = ["read"] }
path "kv/metadata/*" { capabilities = ["read", "list"] }
EOF
bao write auth/kubernetes/role/external-secrets \
  bound_service_account_names=external-secrets-vault \
  bound_service_account_namespaces=external-secrets \
  policies=external-secrets ttl=1h
```

## Usage

### Bootstrap

1. Initialise, unseal and configure the engine and ESO's auth. Safe to re-run:
   each configuration step is skipped if already in place, and an initialised
   cluster is left alone.

    ```bash
    make bao-init
    ```

    !!! danger "Move the unseal keys before you do anything else"
        `make bao-init` writes the 5 unseal keys and the root token to `output/credentials/openbao-init.json` (mode `0600`, gitignored) — a plaintext copy of the keys to every secret the cluster holds, next to the [etcd encryption key](../architecture/security.md). Copy them into a password manager that does not need this cluster to be running, then delete the file. Losing all five means the data is unrecoverable: no support line, no recovery flow.

2. Populate the paths in the [KV layout](#kv-layout) — the prompts follow
   [Quickstart step 11](../quickstart.md):

    ```bash
    make bao-secrets
    ```

3. Confirm the store validates:

    ```bash
    kubectl get clustersecretstore openbao
    ```

### Authenticating locally

```bash
kubectl -n openbao port-forward svc/openbao 8200:8200 &
export BAO_ADDR=http://127.0.0.1:8200
bao login   # paste the root token
```

### Storing and reading a secret

```bash
bao kv put kv/cert-manager/route53 access-key-id='AKIA...' secret-access-key='...'
bao kv put -mount=kv cert-manager/route53 @/tmp/secret.json   # what make bao-secrets does
bao kv get kv/cert-manager/route53
```

### Rotating a credential

`make bao-secrets` is also the rotation tool: it asks, per existing path,
whether to overwrite it.

1. Run it and answer `y` for the path to rotate; the others are left alone.
   `FORCE=1 make bao-secrets` overwrites every ordinary path without asking.
   `authentik/config` and `nextcloud/config` are excepted: they only rewrite
   after a typed `OVERWRITE`, even under `FORCE=1`, because other live
   components authenticate against their contents and a rewrite is an outage
   rather than an inconvenience. Without a terminal every existing path is
   left alone.

    ```bash
    make bao-secrets
    ```

2. Push the new value into the `Secret` now rather than at the next hourly
   refresh — see [External Secrets](external-secrets.md#usage):

    ```bash
    kubectl annotate externalsecret -n <namespace> <name> force-sync=$(date +%s) --overwrite
    ```

3. Restart whatever read the old value into an environment variable; a
   rotated `Secret` reaches a running pod only through a rollout.

## Health check

```bash
kubectl -n openbao exec -it openbao-0 -- bao status
kubectl get clustersecretstore openbao -o yaml
kubectl get externalsecret -A
```

`bao status` should show `Initialized: true`, `Sealed: false`, and
`HA Mode: active` on one pod with `standby` on the others; it exits `0`
unsealed, `2` sealed and `1` unreachable. The `ClusterSecretStore` should
report `Ready=True`.

## Pitfalls

!!! warning "Sealed after every restart, and Ready does not mean unsealed"
    OpenBao seals itself on every pod restart — node reboot, chart bump, Kured — and no auto-unseal is configured, so it stays shut until someone with the key shares unseals it. The readiness probe answers healthy while sealed (`sealedcode=204`), deliberately, so a StatefulSet rollout does not stop at the first pod waiting for keys; the cost is that only `bao status`, not `kubectl get pods`, can tell you OpenBao is usable. While sealed no `ExternalSecret` resolves, so cert-manager loses its Route53 credentials and nothing breaks until a certificate expires up to sixty days later, with no obvious link to the reboot. See [Limitations](../architecture/limitations.md).

!!! warning "`#` and `!` on a command line"
    Inside double quotes an interactive bash expands `!` from history before the quotes are considered, and an unquoted `#` truncates the line. Both are silent and store a plausible credential that does not work. Use single quotes, or write the secret as JSON and pass `@file` — a missing file then fails loudly.

## Recovery

### Unsealing after a restart

```bash
make bao-unseal
```

It feeds three shares from `output/credentials/openbao-init.json` to each sealed
replica, waits for one that has not joined Raft yet, and is idempotent. With
the key file deleted, which is the correct end state, unseal by hand:

```bash
for pod in openbao-0 openbao-1 openbao-2; do
  kubectl -n openbao exec "$pod" -- bao status >/dev/null 2>&1
  case $? in
    0) echo "$pod: already unsealed" ;;
    2) for i in 1 2 3; do
         kubectl -n openbao exec -it "$pod" -- bao operator unseal
       done ;;
    *) echo "$pod: OpenBao did not answer, check the pod" ;;
  esac
done
```

A replica that says `Vault is not initialized` has not joined the Raft cluster
— one that predates the `retry_join` stanzas or started before `openbao-0` was
initialised. Point it at the leader once; it takes unseal keys afterwards:

```bash
kubectl -n openbao exec openbao-1 -- \
  bao operator raft join http://openbao-0.openbao-internal:8200
```

### Backups

```bash
bao operator raft snapshot save snapshot.bao
```

The snapshot holds all KV data and OpenBao's own configuration; restore with
`bao operator raft snapshot restore`. Store it off-cluster — it is encrypted
with a key that exists only in the five shares, so it is exactly as
recoverable as your key custody. See [Backups & Recovery](../operations/backups.md).
