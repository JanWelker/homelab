---
name: alert-review
description: Review what Alertmanager sent over a period and cut the noise without losing signal, by fixing the cause or the rule in YAML, never by a silence. Use when the user says "i received a lot of alertmanager emails the last days, analyze the alarms", "can we reduce the alarms or are they all important", "we have some wanted drops we can not turn off, can we ignore those from alerts while keeping all other drops", "make sure we have alerting (incl. email) for X like for Y", "disable the alarms for <component>", or "do i get alerts for <event>". Covers pulling the alert history, grouping it, sorting each alert into real problem / wanted event / bad rule, and where each fix goes. For a single firing alert use argo-triage; for turning Hubble verdicts into policy rules use network-policy-audit.
---

# alert-review

Routing, grouping and the mail limits are in
`docs/platform/monitoring.md#alerting`; log alerts in
`docs/platform/logging.md#alerting`. Rules live with their component
(`PrometheusRule` next to it, `payload/platform/logging/loki-rules.yaml` for
log alerts). Every fix is a PR; no `amtool silence`, no `kubectl edit`.

## 1. Pull the history

```bash
export KUBECONFIG=$PWD/output/kubeconfig
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090 &
# every alert that fired in the window, with how long
curl -s 'localhost:9090/api/v1/query' --data-urlencode \
  'query=count_over_time(ALERTS{alertstate="firing"}[7d])' | jq -r '.data.result[] | [.metric.alertname, .metric.namespace, .value[1]] | @tsv' | sort -k3 -nr
```

Log alerts come from Loki's ruler and show up in `ALERTS` only if the ruler
remote-writes; otherwise query Alertmanager (`/api/v2/alerts`) for what is
active and the user's mail for what was sent. Ask for a pasted mail when the
window predates Prometheus' retention.

## 2. Sort each alert

| Kind | Sign | Fix |
| --- | --- | --- |
| Real problem | It points at a broken thing | Fix the thing (`argo-triage`), keep the alert |
| Symptom of another alert | Fires with a cause alert in the same namespace | Inhibit rule in `alertmanager-config.yaml`; the chart's defaults are not inherited |
| Wanted event | Expected and cannot be switched off upstream (an app's phone-home that is a DB setting, a nightly job's in-progress gate) | Exclude exactly that case in the rule's expression (a `dst_name` or `requestURI` filter), or route to `null` when the alert exists as a gate; document why in the component's page |
| Bad rule | Fires on a state that is fine (an old failed Job object, a label that never resolves) | Rewrite the expression; for a chart rule, disable it (`defaultRules.disabled`) and redefine it next to its component |
| Flapping | Fires and resolves inside `group_interval` | Add or lengthen `for:`; check the threshold against a week of the metric |

Telemetry and version checks are switched off in config, never allowed by a
`toFQDNs` rule; a drop of one is wanted only until the switch lands.

## 3. Prove the change

Run the new expression against the same window before committing (Prometheus
`query_range`, or Loki through a port-forward): it must stay silent for the
noise and still match one real case. For a missing alert ("alert me on host
firewall drops too"), show it would have fired on a past event.

## 4. Report

A table: alert, count in the window, kind, fix (PR or "keep"). Then the
alerts left untouched and why, so the user can overrule.
