# cert-manager

TLS certificate automation via Let's Encrypt, using DNS-01 challenges through AWS Route53.

## Components

- **ClusterIssuers**: Both staging (testing) and production issuers using DNS-01 via Route53.
- **Certificates**: Wildcard TLS certs for `*.k8s.wlkr.ch` and `*.infra.k8s.wlkr.ch`, stored as Secrets in `kube-system` and referenced by the Gateways.

## AWS Credentials Setup

The DNS-01 solver needs AWS credentials with Route53 permissions. The `route53-credentials` Secret is materialised from [OpenBao](openbao.md) via an [ExternalSecret](external-secrets.md).

Store the credentials in OpenBao once OpenBao and ESO are up:

```bash
bao kv put kv/cert-manager/route53 \
  access-key-id="YOUR_AWS_ACCESS_KEY_ID" \
  secret-access-key="YOUR_AWS_SECRET_ACCESS_KEY"
```

ESO will then create the `route53-credentials` Secret in the `cert-manager` namespace within `refreshInterval` (1h by default). Trigger an immediate sync with:

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
    Until OpenBao is initialised, unsealed, and the secret is stored, cert-manager will fail to issue certificates. For the very first bootstrap, see the [Quickstart](../quickstart.md) which walks through the order.

## Issuers

| Issuer | Purpose |
| --- | --- |
| `letsencrypt-staging` | Testing — issues untrusted certs, no rate limits |
| `letsencrypt-prod` | Production — issues trusted certs, subject to rate limits |

Use `letsencrypt-staging` first when setting up to avoid hitting Let's Encrypt rate limits.

## Directory Structure

```text
cert-manager/                  # TLS Certificate Management
├── application.yaml           # ArgoCD Application (Helm chart)
├── cluster-issuers.yaml       # Let's Encrypt staging + prod issuers
├── certificates.yaml          # All Certificate resources
└── route53-credentials.yaml   # ExternalSecret → OpenBao
```
