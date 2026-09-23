---
description: "Automated TLS certificates from Let's Encrypt using cert-manager with a Route53 DNS-01 solver."
---

# cert-manager

TLS certificate automation via Let's Encrypt, using DNS-01 challenges through
AWS Route53. DNS-01 because these hostnames resolve to RFC1918 addresses that
Let's Encrypt cannot reach, and because it is the only way to get a wildcard:
two certificates, `*.k8s.wlkr.ch` and `*.infra.k8s.wlkr.ch`, cover every
hostname the cluster serves.

## At a glance

| | |
| --- | --- |
| Namespace | `cert-manager`; the certificates it issues land in `kube-system` |
| Depends on | [External Secrets](external-secrets.md) for the Route53 credential, so transitively on [OpenBao](openbao.md) |
| If it is down | Nothing immediately. Certificates stop renewing, and the consequence surfaces up to sixty days later |
| Health check | `kubectl get certificate -A` &rarr; all `READY=True` |
| Files | `payload/platform/cert-manager/`, `payload/platform/certificates/` |

## Configuration

| Setting | Why |
| --- | --- |
| Issuers and certificates in their own `certificates` Application | Kept with cert-manager, the controller would read Degraded until OpenBao held the Route53 credentials — see [Bootstrap convergence](../architecture/gitops.md#bootstrap-convergence) |
| Three sync waves inside `certificates`: `ExternalSecret`, then the ClusterIssuers, then the Certificates | In one wave ArgoCD orders custom resources alphabetically — `Certificate`, `ClusterIssuer`, `ExternalSecret`, exactly backwards — and an issuer applied before its Secret stays `Ready=False` with `InvalidSolver` until something resyncs it |
| Sync `retry` on both Applications | Without one a failed apply ends the operation where it fell; one flake at the front of the chain, such as the ESO webhook being unreachable on a fresh CNI, leaves every issuer and certificate behind it unmade |
| `ServiceMonitor` rendered unconditionally | The chart cannot sync until the Prometheus operator CRDs exist, which is why they are the separate `prometheus-operator-crds` Application — see [Monitoring](monitoring.md#crds) |
| `letsencrypt-staging` and `letsencrypt-prod` | Use staging first: production allows five duplicate certificates per week, a misconfigured solver retries until that is gone, and there is no appeals process |

The IAM user needs at minimum:

```json
{
  "Effect": "Allow",
  "Action": ["route53:GetChange", "route53:ChangeResourceRecordSets", "route53:ListHostedZonesByName"],
  "Resource": "*"
}
```

## Usage

Store the credentials once OpenBao and ESO are up; `make bao-secrets` prompts
for them, or by hand:

```bash
bao kv put kv/cert-manager/route53 \
  access-key-id="YOUR_AWS_ACCESS_KEY_ID" \
  secret-access-key="YOUR_AWS_SECRET_ACCESS_KEY"
```

ESO creates the `route53-credentials` Secret within its `refreshInterval`, or
[immediately on request](external-secrets.md#adding-a-secret).

## Health check

Work down the chain of custody; the answer is nearly always further back than
the `Certificate`:

```bash
kubectl describe certificate -n kube-system <name>
kubectl get certificaterequest,order,challenge -A
kubectl -n cert-manager logs deploy/cert-manager --tail=100
```

A `Challenge` stuck in `pending` is a DNS problem: either the credentials
cannot write to the zone, or the TXT record is there and the resolver has not
caught up. `dig +short TXT _acme-challenge.<host>` settles which.

## Pitfalls

!!! note "A sealed OpenBao means no renewals"
    Until OpenBao is unsealed and the secret stored, cert-manager cannot issue or renew, and the failure surfaces as an expired certificate roughly two months later — see [OpenBao](openbao.md#pitfalls).
