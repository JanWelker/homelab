---
description: "Automated TLS certificates from Let's Encrypt using cert-manager with a Route53 DNS-01 solver."
---

# cert-manager

TLS certificate automation via Let's Encrypt, using DNS-01 challenges through
AWS Route53. DNS-01 because these hostnames resolve to RFC1918 addresses that
Let's Encrypt cannot reach, and because it is the only way to get a wildcard:
one certificate per zone, in `payload/platform/certificates/certificates.yaml`,
covers every Gateway hostname.

## At a glance

| | |
| --- | --- |
| Namespace | `cert-manager`; the wildcards land in `kube-system` beside the Gateways, the agent sessions' in their own namespace; see [SSH listener](gateway-api.md#ssh-listener) |
| Depends on | [External Secrets](external-secrets.md) for the Route53 credential, so transitively on [OpenBao](openbao.md) |
| If it is down | Nothing immediately. Certificates stop renewing; the consequence surfaces up to sixty days later |
| Health check | `kubectl get certificate -A` &rarr; all `READY=True` |
| Files | `payload/platform/cert-manager/`, `payload/platform/certificates/` |

## Configuration

| Setting | Why |
| --- | --- |
| Issuers and certificates in their own `certificates` Application | Kept with cert-manager, the controller reads Degraded until OpenBao holds the Route53 credentials — see [Bootstrap convergence](../architecture/gitops.md#bootstrap-convergence) |
| Three sync waves inside `certificates`: `ExternalSecret`, then the ClusterIssuers, then the Certificates | One wave orders custom resources alphabetically, exactly backwards, and an issuer applied before its Secret stays `Ready=False` with `InvalidSolver` |
| Sync `retry` on both Applications | Without one a failed apply, such as the ESO webhook being unreachable on a fresh CNI, ends the operation and leaves everything behind it unmade |
| `ServiceMonitor` rendered unconditionally | The chart cannot sync until the Prometheus operator CRDs exist, which is why they are the separate `prometheus-operator-crds` Application — see [Monitoring](monitoring.md#crds) |
| Two webhook replicas, a PodDisruptionBudget and a hostname spread | The webhook's `failurePolicy` is `Fail`, so a lone replica rescheduling through a drain refuses every apply — see [Replica placement](index.md#replica-placement) |
| `letsencrypt-staging` and `letsencrypt-prod` | Use staging first: production allows five duplicate certificates per week and a misconfigured solver retries until they are gone |

The IAM policy is the
[Route53 policy](https://cert-manager.io/docs/configuration/acme/dns01/route53/#set-up-an-iam-policy)
cert-manager documents, narrowed to `_acme-challenge` TXT records so a stolen key cannot repoint a hostname. `ListHostedZonesByName` stays because the
issuers do not set `hostedZoneID`. It lives in AWS, so a change is applied there by hand:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "route53:GetChange",
      "Resource": "arn:aws:route53:::change/*"
    },
    {
      "Effect": "Allow",
      "Action": "route53:ChangeResourceRecordSets",
      "Resource": ["arn:aws:route53:::hostedzone/<zone-id>"],
      "Condition": {
        "ForAllValues:StringEquals": {
          "route53:ChangeResourceRecordSetsRecordTypes": ["TXT"]
        },
        "ForAllValues:StringLike": {
          "route53:ChangeResourceRecordSetsNormalizedRecordNames": ["_acme-challenge.*"]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": "route53:ListResourceRecordSets",
      "Resource": ["arn:aws:route53:::hostedzone/<zone-id>"]
    },
    {
      "Effect": "Allow",
      "Action": "route53:ListHostedZonesByName",
      "Resource": "*"
    }
  ]
}
```

List one `hostedzone/<zone-id>` ARN per zone a Certificate names.

### Issuer scope

`certificate-issuer-scope` lets only `kube-system`, and `claude-agents` for
`*.ssh.wlkr.ch`, use a ClusterIssuer, which signs any name in the zones; see
[policies](security-policies.md#admission-policies).

### Pods from the controllers

`controller-pod-limits` refuses pods from the cert-manager controller, which
solves DNS-01 only, and host access for CloudNativePG's. Confining those to
namespaces holding a `Cluster` is not expressible in a policy.

## Usage

Once OpenBao and ESO are up, `make bao-secrets` prompts for the credentials, or by hand:

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

A `Challenge` stuck in `pending` is a DNS problem: the credentials cannot
write to the zone, or the resolver has not caught up. `dig +short TXT
_acme-challenge.<host>` settles which.

## Pitfalls

!!! note "A sealed OpenBao means no renewals"
    Until OpenBao is unsealed and the secret stored, nothing issues or renews, and the failure surfaces as an expired certificate two months later — see [OpenBao](openbao.md#pitfalls).
