---
name: argo-triage
description: Diagnose an unhealthy Argo CD Application or a broken platform component on this cluster and fix it in the repository, never on the cluster. Use when the user says "what is wrong with kube-prometheus-stack", "what is going on with my openbao deployment", "some argo apps are still unhappy", "rook is still unhappy", "the two argo apps are hanging", "alerts for nextcloud are firing, investigate", pastes an alert's summary or description with no question ("X was refused 18 times in ten minutes", "A burst of 403s from one identity is what probing RBAC looks like"), "why is this alert firing", "I see no logs in Loki", "refresh the argo app so it syncs now", "merge it and watch the rollout", or reports any Degraded, OutOfSync, Progressing-forever or Missing state. Covers reading Application status, tracing an alert back to its rule and the job behind it, the ExternalSecret and OpenBao chain, events and logs, the declarative fix, and watching the sync land.
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

## 1a. From an alert

A pasted alert is a symptom with an identity attached; the job it fired
during is usually the patient.

1. Find the rule: Loki-ruler alerts live in
   `payload/platform/logging/loki-rules.yaml`, Prometheus rules under
   `payload/platform/monitoring/`. Grep a phrase from the description.
2. Re-run the rule's query for that identity with the raw lines, and count
   the previous days before calling anything new:

    ```bash
    kubectl -n logging port-forward svc/loki 3100:3100 &
    curl -sG http://127.0.0.1:3100/loki/api/v1/query_range \
      --data-urlencode 'query={job="kubernetes-audit"} | json | responseStatus_code = 403 | user_username="system:serviceaccount:<ns>:<sa>"' \
      --data-urlencode "start=$(( $(date +%s) - 86400 ))000000000" --data-urlencode "end=$(date +%s)000000000" \
      | jq -r '.data.result[].values[][1]' | jq -r '[.userAgent, .verb, .requestURI, .responseStatus.message] | @tsv'
    ```

    An audit entry names the client in `userAgent` (`csi-provisioner`, not
    the pod), the object, and why it was refused. A `count_over_time(...[1d])`
    with `step=86400` shows whether it fires every night.
3. Line the timestamps up with the schedules: 01:00 is the etcd CronJob,
   02:00 is Velero. Read that job's own result before the alert's: a
   nightly 403 burst turned out to be noise beside a backup that had never
   moved volume data.
4. Streams worth knowing: `{job="kubernetes-audit"}`, `{job="hubble"}` (fields
   under `flow.`), and pod logs by `{namespace="..."}`. RGW's access log is
   `{namespace="rook-ceph"} |= "/velero/"`; a `-` in its user column is a
   request that failed authentication, not authorization.

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
- **Velero PartiallyFailed, every `BackupRepository` NotReady, DataUploads
  `Access Denied`**: `kubectl get backup` is CloudNativePG's kind; Velero's
  is `backups.velero.io`, then `backuprepositories` and `datauploads`. The
  RGW cause and the TLS fix are in `docs/platform/velero.md` (Pitfalls).
- **403 burst from `rbd-ctrlplugin-sa` at 02:01**: csi-provisioner's clone
  finalizer, `docs/platform/rook-ceph.md` (CSI driver). The local verb goes
  once ceph/ceph-csi-operator#625 ships in a release Rook pulls in.
- **Workload Applications in `homelab-apps`** follow the same rules; the
  fix goes in that repository, with the same syncPolicy block.

## 4. Fix in the repository

Find the declarative place: the Helm value, the manifest, or Argo CD's
`resource.customizations` for a health check that is wrong rather than the
resource. Ship it through `pr-batch`, one PR per cause. Say in the PR body
whether the user must restart or unseal something after merge.

Do not `kubectl edit`, `patch`, `delete` or `helm upgrade` on a running
cluster. Read-only commands, `argocd app sync` on an exhausted retry,
`argocd app get --refresh`, and a throwaway probe pod are the whole
imperative toolbox:

```bash
kubectl -n <ns> run probe --rm -i --restart=Never --image=curlimages/curl:8.11.1 \
  --command -- curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' https://<host>/
```

Reproduce a connection or lookup from the namespace in question before
blaming a policy; Hubble then shows the verdict and the destination
identity (a Gateway address is `world`, so its rule is `toFQDNs`, see
`docs/platform/security-policies.md`). While
`policy-audit-mode` in the `cilium-config` ConfigMap is `true`, a
`DROPPED` verdict in Loki is what would happen, not what did.

## 5. Watch it land

After the merge, one Monitor on the Application's sync and health, plus the
resource that was broken, rather than repeated `get` calls:

```bash
kubectl -n argocd get application <app> -o jsonpath='{.status.sync.status} {.status.health.status} {.status.sync.revision}'
```

A PR that adds an HTTPRoute is done when the route reports `Accepted` and
external-dns has published the name (`dig`); a PR that points a client at
that name waits for both before it merges.

Report the revision Argo reached, the health state, and what is still
pending on the user (unseal, restart, credentials). If the state did not
change, say so with the message Argo gives, and do not retry the same fix.
