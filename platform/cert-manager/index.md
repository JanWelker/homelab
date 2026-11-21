# cert-manager

TLS certificate automation via Let's Encrypt, using DNS-01 challenges through AWS Route53. DNS-01 because these hostnames resolve to RFC1918 addresses that Let's Encrypt cannot reach, and because it is the only way to get a wildcard: one certificate per zone, listed in `payload/platform/certificates/certificates.yaml`, covers every hostname the Gateways terminate.

## At a glance

|  |  |
| --- | --- |
| Namespace | `cert-manager`; the wildcards land in `kube-system` beside the Gateways, the agent sessions' certificates in their own namespaces; see [SSH listener](<https://homelab.wlkr.ch/platform/gateway-api/index.md#ssh-listener>) |
| Depends on | [External Secrets](<https://homelab.wlkr.ch/platform/external-secrets/index.md>) for the Route53 credential, so transitively on [OpenBao](<https://homelab.wlkr.ch/platform/openbao/index.md>) |
| If it is down | Nothing immediately. Certificates stop renewing, and the consequence surfaces up to sixty days later |
| Health check | `kubectl get certificate -A` → all `READY=True` |
| Files | `payload/platform/cert-manager/`, `payload/platform/certificates/` |

## Configuration

| Setting | Why |
| --- | --- |
| Issuers and certificates in their own `certificates` Application | Kept with cert-manager, the controller would read Degraded until OpenBao held the Route53 credentials — see [Bootstrap convergence](<https://homelab.wlkr.ch/architecture/gitops/index.md#bootstrap-convergence>) |
| Three sync waves inside `certificates`: `ExternalSecret`, then the ClusterIssuers, then the Certificates | In one wave ArgoCD orders custom resources alphabetically — `Certificate`, `ClusterIssuer`, `ExternalSecret`, exactly backwards — and an issuer applied before its Secret stays `Ready=False` with `InvalidSolver` until something resyncs it |
| Sync `retry` on both Applications | Without one a failed apply ends the operation where it fell; one flake at the front of the chain, such as the ESO webhook being unreachable on a fresh CNI, leaves every issuer and certificate behind it unmade |
| `ServiceMonitor` rendered unconditionally | The chart cannot sync until the Prometheus operator CRDs exist, which is why they are the separate `prometheus-operator-crds` Application — see [Monitoring](<https://homelab.wlkr.ch/platform/monitoring/index.md#crds>) |
| Two webhook replicas, a PodDisruptionBudget and a hostname spread | The webhook's `failurePolicy` is `Fail`, so while a single replica reschedules through a Kured drain every Certificate and Issuer apply is refused — see [Replica placement](<https://homelab.wlkr.ch/platform/index.md#replica-placement>) |
| `letsencrypt-staging` and `letsencrypt-prod` | Use staging first: production allows five duplicate certificates per week, a misconfigured solver retries until that is gone, and there is no appeals process |

The IAM user's policy is cert-manager's [Route53 policy](<https://cert-manager.io/docs/configuration/acme/dns01/route53/#set-up-an-iam-policy>) narrowed to `_acme-challenge` TXT records in this account's zones, so a stolen key cannot repoint a hostname. `ListHostedZonesByName` stays because the issuers in `cluster-issuers.yaml` do not set `hostedZoneID`. The policy lives in AWS, so a change here is applied there by hand:

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

## Usage

Store the credentials once OpenBao and ESO are up; `make bao-secrets` prompts for them, or by hand:

```bash
bao kv put kv/cert-manager/route53 \
  access-key-id="YOUR_AWS_ACCESS_KEY_ID" \
  secret-access-key="YOUR_AWS_SECRET_ACCESS_KEY"
```

ESO creates the `route53-credentials` Secret within its `refreshInterval`, or [immediately on request](<https://homelab.wlkr.ch/platform/external-secrets/index.md#adding-a-secret>).

## Health check

Work down the chain of custody; the answer is nearly always further back than the `Certificate`:

```bash
kubectl describe certificate -n kube-system <name>
kubectl get certificaterequest,order,challenge -A
kubectl -n cert-manager logs deploy/cert-manager --tail=100
```

A `Challenge` stuck in `pending` is a DNS problem: either the credentials cannot write to the zone, or the TXT record is there and the resolver has not caught up. `dig +short TXT _acme-challenge.<host>` settles which.

## Pitfalls

> [!NOTE]
>
> **A sealed OpenBao means no renewals**
>
> Until OpenBao is unsealed and the secret stored, cert-manager cannot issue or renew, and the failure surfaces as an expired certificate roughly two months later — see [OpenBao](<https://homelab.wlkr.ch/platform/openbao/index.md#pitfalls>).
