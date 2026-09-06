---
description: "Automated TLS certificates from Let’s Encrypt using cert-manager with a Route53 DNS-01 solver."
---

# cert-manager

TLS certificate automation via Let's Encrypt, using DNS-01 challenges through AWS Route53.

DNS-01 rather than HTTP-01 for one decisive reason: these hostnames resolve to
RFC1918 addresses that Let's Encrypt cannot reach. Nothing on the public internet
can complete an HTTP challenge against `10.9.2.248`. Proving control of the DNS
zone works from anywhere, and it is also the only way to get a wildcard.

## Components

- **ClusterIssuers**: Both staging (testing) and production issuers using DNS-01 via Route53.
- **Certificates**: Wildcard TLS certs for `*.k8s.wlkr.ch` and `*.infra.k8s.wlkr.ch`, stored as Secrets in `kube-system` and referenced by the Gateways. Two certificates cover every hostname this cluster will ever serve, which is a pleasant place to be.

## AWS Credentials Setup

The DNS-01 solver needs AWS credentials with Route53 permissions. The `route53-credentials` Secret is materialised from [OpenBao](openbao.md) via an [ExternalSecret](external-secrets.md).

Store the credentials in OpenBao once OpenBao and ESO are up:

```bash
bao kv put kv/cert-manager/route53 \
  access-key-id="YOUR_AWS_ACCESS_KEY_ID" \
  secret-access-key="YOUR_AWS_SECRET_ACCESS_KEY"
```

ESO will then create the `route53-credentials` Secret in the `cert-manager` namespace within `refreshInterval` (1h by default) — or, if you would rather not spend an hour wondering whether it worked, immediately:

```bash
kubectl annotate externalsecret -n cert-manager route53-credentials \
  force-sync=$(date +%s) --overwrite
```

The IAM user needs at minimum:

```json
{
  "Effect": "Allow",
  "Action": ["route53:GetChange", "route53:ChangeResourceRecordSets", "route53:ListHostedZonesByName"],
  "Resource": "*"
}
```

!!! note
    Until OpenBao is initialised, unsealed, and the secret is stored, cert-manager will fail to issue certificates. This is the dependency that catches people after every power cut: sealed OpenBao means no Route53 credentials, which means no renewals, which means an expired certificate roughly two months later with no obvious connection to the outage that caused it. For the very first bootstrap, see the [Quickstart](../quickstart.md) which walks through the order.

## Issuers

| Issuer | Purpose |
| --- | --- |
| `letsencrypt-staging` | Testing — issues untrusted certs, no rate limits |
| `letsencrypt-prod` | Production — issues trusted certs, subject to rate limits |

Use `letsencrypt-staging` first when setting up. Production allows five duplicate certificates per week, a misconfigured solver will retry cheerfully until that is gone, and then you wait — there is no appeals process and no amount of restarting the pod helps. Staging exists exactly so you can get it wrong as many times as you need to.

## When a certificate will not issue

Work down the chain of custody; the answer is nearly always further back than the `Certificate` itself:

```bash
kubectl describe certificate -n kube-system <name>
kubectl get certificaterequest,order,challenge -A
kubectl -n cert-manager logs deploy/cert-manager --tail=100
```

A `Challenge` stuck in `pending` is a DNS problem, not a cert-manager problem: either the credentials cannot write to the zone, or the TXT record is there and the resolver has not caught up yet. `dig +short TXT _acme-challenge.<host>` settles which.

## Directory Structure

```text
cert-manager/                  # TLS Certificate Management
├── application.yaml           # ArgoCD Application (Helm chart)
├── cluster-issuers.yaml       # Let's Encrypt staging + prod issuers
├── certificates.yaml          # All Certificate resources
└── route53-credentials.yaml   # ExternalSecret → OpenBao
```
