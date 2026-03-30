# cert-manager

TLS certificate automation via Let's Encrypt, using DNS-01 challenges through AWS Route53.

## Components

- **ClusterIssuers**: Both staging (testing) and production issuers using DNS-01 via Route53.
- **Certificates**: Wildcard TLS certs for `*.k8s.wlkr.ch` and `*.infra.k8s.wlkr.ch`, stored as Secrets in `kube-system` and referenced by the Gateways.

## AWS Credentials Setup

The DNS-01 solver needs AWS credentials with Route53 permissions. A template is provided at `route53-credentials.yaml.template`.

Copy and fill it in before bootstrapping:

```bash
cp payload/platform/cert-manager/route53-credentials.yaml.template \
   payload/platform/cert-manager/route53-credentials.yaml
```

The Secret must contain:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: route53-credentials
  namespace: cert-manager
stringData:
  access-key-id: "YOUR_AWS_ACCESS_KEY_ID"
  secret-access-key: "YOUR_AWS_SECRET_ACCESS_KEY"
```

The IAM user needs at minimum:

```json
{
  "Effect": "Allow",
  "Action": ["route53:GetChange", "route53:ChangeResourceRecordSets", "route53:ListHostedZonesByName"],
  "Resource": "*"
}
```

!!! warning
    `route53-credentials.yaml` is gitignored. Never commit real credentials.

## Issuers

| Issuer | Purpose |
| --- | --- |
| `letsencrypt-staging` | Testing — issues untrusted certs, no rate limits |
| `letsencrypt-prod` | Production — issues trusted certs, subject to rate limits |

Use `letsencrypt-staging` first when setting up to avoid hitting Let's Encrypt rate limits.

## Directory Structure

```text
cert-manager/          # TLS Certificate Management
├── application.yaml   # ArgoCD Application (Helm chart)
├── cluster-issuers.yaml # Let's Encrypt staging + prod issuers
├── certificates.yaml  # All Certificate resources
└── route53-credentials.yaml.template
```
