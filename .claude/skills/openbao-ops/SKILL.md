---
name: openbao-ops
description: Initialise, unseal, populate or repair OpenBao on this cluster and the External Secrets chain that depends on it. Use when the user says "what is going on with my openbao deployment", "how can I load the keys into openbao", "bao-secrets did not create the nextcloud secrets", "make bao-audit fails", "openbao restarted and unsealed, continue", "add the X secret to make bao-secrets", "external secrets refreshes every hour, how can I refresh it on demand", "some of the tokens have special characters", or any ExternalSecret, ClusterSecretStore or certificate that is Degraded after a restart. Covers the make targets, what each failure looks like, adding a new KV path, and the restart handshake with the user.
---

# openbao-ops

Sealed OpenBao looks like six unrelated things breaking at once, and it seals
on every pod restart. The runbook, KV layout and the reasons are in
`docs/platform/openbao.md`; this skill is what to do in a session.

## 1. Status first

```bash
for p in openbao-0 openbao-1 openbao-2; do kubectl -n openbao exec $p -- bao status 2>&1 | grep -E 'Initialized|Sealed|HA Mode|not initialized' | tr '\n' ' '; echo " $p"; done
kubectl get clustersecretstore openbao
kubectl get externalsecret -A | grep -v SecretSynced
```

Exit codes: 0 unsealed, 2 sealed, 1 unreachable. `Vault is not initialized`
on one replica means it has not joined Raft; `bao operator raft join` to
`openbao-0` once, then it takes unseal keys.

## 2. The three make targets

| Target | When | Idempotent |
| --- | --- | --- |
| `make bao-init` | fresh cluster, once | yes: skips an initialised cluster and each configured step |
| `make bao-unseal` | after any pod restart, node reboot, Kured, chart bump | yes: reads shares from `output/credentials/openbao-init.json` |
| `make bao-secrets` | fresh cluster, or when a new KV path is added | asks per existing path; `FORCE=1` overwrites |

The user runs them. They hold the key shares; Claude does not read
`output/credentials/`. When a merge needs a restart or an unseal, say so in
the PR body and stop with "waiting for you to restart and unseal", then
continue on their "unsealed, continue".

## 3. What ESO does after an unseal

Nothing, for up to the refresh interval. To make an `ExternalSecret`
re-fetch now, the documented nudge is a `force-sync` annotation:

```bash
kubectl -n <ns> annotate externalsecret <name> force-sync=$(date +%s) --overwrite
```

That is a read-side trigger, not a state change, and is the one imperative
command acceptable here. Certificates that stayed Degraded after the Secret
arrived need cert-manager to retry; wait one issuer backoff before touching
them.

## 4. Adding a secret

A new consumer means, in one PR:

1. A leaf `<workload>/<purpose>` in the KV layout table of the OpenBao page,
   with the `bao kv put` line in a comment at the top of the `ExternalSecret`
   that reads it. Its own path, not more keys on an existing one: a `kv put`
   replaces the path wholesale, so shared paths make every addition an outage
   for the other readers.
2. The generation or prompt in `scripts/bao-secrets.sh`. Generated values
   are generated there; values from outside accounts are prompted with
   `read -rs`, never taken from the command line, because `#` and `!` are
   mangled silently. Existing paths are asked about before overwrite; keep
   that.
3. The `ExternalSecret` and a `ClusterSecretStore` reference, in the
   component's directory, or in `homelab-apps` for a workload.

Then the user runs `make bao-secrets` and answers the prompts; "did not
create the nextcloud secrets" is usually the script skipping an existing
path or a prompt that was not reached, so read its output rather than the
script.

## 5. Things the API refuses

Audit devices cannot be enabled over the API on this build; they are
`audit` stanzas in the server config and take effect on the next pod
restart, which `OnDelete` leaves to the user. Same for any listener or
storage change. Say "applies at the next restart you do" and do not offer a
rollout restart.

## 6. Report

Sealed or not per replica, store Ready or not, the count of ExternalSecrets
still not synced, and exactly what the user must run. Nothing else is done
on the cluster.
