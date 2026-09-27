---
description: "Dispositions from the network policy audit week of 2026-09-21 to 2026-09-27: what became a rule, what became a config switch, and what stays as it is."
---

# Network Policy Audit 2026-09-27

One week of `AUDIT` verdicts from every namespace, read at the end of the
[rollout](../platform/security-policies.md#rollout), kept so the flip to
enforcement starts from the disposition rather than the raw count. 25k
verdicts, 21k of them one missing pair of rules.

## Rules added

| Flow | Disposition |
| --- | --- |
| Prometheus and the CloudNativePG operator to Authentik's database on 9187 and 8000 | **Rule.** The database moved in #906 and the policy's spec never followed its description: #927 |
| Every `http: [{}]` with a machine caller | **Tightened** to the methods and paths the week recorded: `GET /metrics` for every scrape, the four calls External Secrets makes at OpenBao, Loki's `POST /api/v2/alerts`, Grafana's `GET /loki/api/v1/…`: #928, apps #59. UIs behind the Authentik outpost stay open |

## Config switches

| Flow | Disposition |
| --- | --- |
| Authentik server to `goauthentik.io`, twice per pod start | **Switched off.** `disable_startup_analytics`: #929 |
| Authentik server to `www.gravatar.com`, 117 times | **Not a switch the repository holds.** `AUTHENTIK_AVATARS` seeds the tenant once; the live value is a system setting and the database still says `gravatar,initials`. Set `initials` under System → Settings; the upstream ask is [goauthentik/authentik#26470](https://github.com/goauthentik/authentik/issues/26470) |
| Nextcloud to `updates.nextcloud.com` and `pushfeed.nextcloud.com` | **Switched off** in the startup hook: apps #60 |
| Trivy scan jobs to `check.trivy.dev`, Authentik worker to `version.goauthentik.io`, Alloy and Loki to `stats.grafana.org`, Grafana to `grafana.com` and `secure.gravatar.com` | **Already off**, by #861, #883 and the values that predate the week; every record is from before the switch landed |

## Nothing to do

| Flow | Why |
| --- | --- |
| A name a `toFQDNs` rule allows, one `SYN` audited now and then: Alertmanager to the mail relay 14 of 254 mails, the repo-server to `release-assets.githubusercontent.com`, Home Assistant to `alerts.home-assistant.io`, scan jobs to the registries | The destination identity in the record is `world`, so the IP was not yet, or no longer, in the name's set when the `SYN` left. Eight fresh lookups from the Home Assistant pod were all allowed. Under enforcement the first `SYN` drops and the retransmit a second later decides it, so the `DROPPED` records after the flip say which: one drop per connection is the DNS proxy's 100ms hand-off, a run of drops on one source port is an application connecting to an address it cached past the TTL |
| Replies on high ports, pod addresses classified `world`, `openbao-N.openbao-internal` during a restart | Answer halves and identities of pods that had just gone; the export marks the first `is_reply` |
| `ceph-object-controller-detect-version` to its own node's API server, 3 times on thor | The host identity there carries `reserved:kube-apiserver`, and the same Job reached the other control-plane nodes hundreds of times; not at an agent restart. Watch for it as `DROPPED` |
| Home Assistant to the node's `cilium_host` address on UDP 53, 3 times; SSDP to `255.255.255.255` before apps #44; `argocd-redis-secret-init` before #871 | Predate their fix, or too rare to name |

## Consolidation

Not done, recorded: `argocd-server` and `argocd-server-oidc` select the
same pods and could be one policy; the intra-namespace pair and the node
rule are in 18 of 19 namespaces and are the next cluster-wide candidates
after DNS, at the cost of the per-namespace file no longer being the whole
record. The Prometheus rule is worded identically in 17 files, which is the
point.
