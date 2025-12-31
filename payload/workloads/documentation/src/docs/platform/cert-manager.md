# cert-manager

TLS certificate automation via Let's Encrypt.

## Components

- **ClusterIssuers**: Both staging (testing) and production issuers using DNS-01 via Route53.
- **Certificates**: Gateway TLS certs for `*.k8s.wlkr.ch` and `*.infra.k8s.wlkr.ch`.

## Directory Structure

```text
cert-manager/          # TLS Certificate Management
├── application.yaml   # ArgoCD Application (Helm chart)
├── cluster-issuers.yaml # Let's Encrypt staging + prod issuers
├── certificates.yaml  # All Certificate resources
└── route53-credentials.yaml.template
```
