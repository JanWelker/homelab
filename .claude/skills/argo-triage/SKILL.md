---
name: argo-triage
description: Diagnose an unhealthy Argo CD Application or a broken platform component on this cluster and fix it in the repository, never on the cluster. Use when the user says "what is wrong with kube-prometheus-stack", "what is going on with my openbao deployment", "some argo apps are still unhappy", "rook is still unhappy", "the two argo apps are hanging", "alerts for nextcloud are firing, investigate", "I see no logs in Loki", "refresh the argo app so it syncs now", "merge it and watch the rollout", or reports any Degraded, OutOfSync, Progressing-forever or Missing state. Covers reading Application status, the ExternalSecret and OpenBao chain, events and logs, the declarative fix, and watching the sync land.
---

# argo-triage

Argo CD owns the cluster from `payload/` and the `homelab-apps` repository.
Diagnosis is read-only kubectl; every fix is a commit. The rules that shape
the answer live in `CLAUDE.md` and `docs/architecture/gitops.md`; this skill
is the order in which to look.

## 0. Access

`export KUBECONFIG=$PWD/output/kubeconfig`. The Argo CD API is LAN-only, so
`argocd` CLI commands work from here and nowhere else; there is no webhook,
Git is polled every 60 seconds.

## 1. Read the Application, not the pod

```bash
kubectl -n argocd get applications
kubectl -n argocd get application <app> -o jsonpath='{.status.conditions}{"\n"}{.status.operationState.message}{"\n"}'
kubectl -n argocd get application <app> -o json | jq '.status.resources[] | select(.health.status!="Healthy" and .health.status!=null) | {kind,namespace,name,status,health}'
```

Sort the state into one of five buckets before reading anything else:

| State | Meaning | Next |
| --- | --- | --- |
| OutOfSync + sync failed | A manifest did not apply: missing CRD, invalid field, immutable change | `operationState.message`, then §3 |
| Synced + Degraded | Applied, a resource cannot go Ready | the resource in `status.resources`, then §2 |
| Synced + Progressing for long | Waiting on a PVC, image pull, or a Job | events on the pod, then §2 |
| Missing resources, prune warnings | A resource moved or was deleted by hand | `docs/architecture/gitops.md` on moving resources |
| Retries exhausted (`retry` limit 10) | Failed against a dependency that arrived later | `argocd app sync <app>` once; nothing to fix |

Every Application auto-syncs with `selfHeal` and `prune`, so a state that
persists is not waiting for a person to click Sync. A hand-applied fix is
undone within one poll.

## 2. The secrets chain is the usual root

Six unrelated Degraded Applications at once means one sealed OpenBao. Check
in this order and stop at the first failure:

```bash
kubectl -n openbao exec openbao-0 -- bao status | grep -E 'Sealed|HA Mode'
kubectl get clustersecretstore openbao
kubectl get externalsecret -A | grep -v SecretSynced
kubectl get certificate -A | grep -v True
```

Sealed: hand it to the `openbao-ops` skill; the user unseals, then wait one
ESO refresh (up to an hour) or ask for an on-demand refresh by annotating
the ExternalSecret, which is a legitimate read-only nudge. A fresh cluster
sits here by design until `make bao-init`, `make bao-unseal` and
`make bao-secrets`.

## 3. Then the resource itself

```bash
kubectl -n <ns> describe <kind> <name> | sed -n '/Events/,$p'
kubectl -n <ns> logs <pod> --previous
kubectl -n <ns> get events --sort-by=.lastTimestamp | tail -20
```

Known shapes on this cluster:

- **Rook HEALTH_WARN after a reboot** blocks anything that needs a new PVC.
  `ceph status` via the toolbox; mute or fix in `rook-ceph-cluster`'s values,
  never with `ceph health mute` by hand.
- **StatefulSet update refused** on an immutable field: the fix is a new
  name or a documented delete-and-recreate PR, and the PVC must be adopted,
  so compare `serviceName`, selector and `volumeClaimTemplates` first.
- **Chart renders a different Secret every sync** (`checksum` restarts,
  permanent OutOfSync): set `existingSecret` and source it from OpenBao.
- **Prometheus alerts point at cluster-internal URLs**: `externalUrl` on
  Prometheus and Alertmanager in `payload/platform/monitoring`.
- **No logs in Loki**: check Alloy's targets and the `job` label before the
  Loki config; the dashboards filter on it.
- **Workload Applications in `homelab-apps`** follow the same rules; the
  fix goes in that repository, with the same syncPolicy block.

## 4. Fix in the repository

Find the declarative place: the Helm value, the manifest, or Argo CD's
`resource.customizations` for a health check that is wrong rather than the
resource. Ship it through `pr-batch`, one PR per cause. Say in the PR body
whether the user must restart or unseal something after merge.

Do not `kubectl edit`, `patch`, `delete` or `helm upgrade` on a running
cluster. Read-only commands, `argocd app sync` on an exhausted retry, and
`argocd app get --refresh` are the whole imperative toolbox.

## 5. Watch it land

After the merge, one Monitor on the Application's sync and health, plus the
resource that was broken, rather than repeated `get` calls:

```bash
kubectl -n argocd get application <app> -o jsonpath='{.status.sync.status} {.status.health.status} {.status.sync.revision}'
```

Report the revision Argo reached, the health state, and what is still
pending on the user (unseal, restart, credentials). If the state did not
change, say so with the message Argo gives, and do not retry the same fix.
