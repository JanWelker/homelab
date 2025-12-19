# Core Infrastructure

This directory contains **core infrastructure components** that are applied after initial cluster bootstrap via `make install-core`.

## Components

| File | Description |
|------|-------------|
| `cilium-values.yaml` | Helm values for Cilium CNI (kube-proxy replacement, WireGuard encryption, Ingress Controller, L2 Announcements) |
| `cilium-pool.yaml` | `CiliumLoadBalancerIPPool` and `CiliumL2AnnouncementPolicy` for shared ingress VIP |
| `cert-manager.yaml` | Cert-Manager CRDs and deployment manifest |
| `cluster-issuer.yaml` | Let's Encrypt **staging** ClusterIssuer (for testing) |
| `cluster-issuer-prod.yaml` | Let's Encrypt **production** ClusterIssuer |
| `argocd-cert.yaml` | Certificate resource for ArgoCD Ingress TLS |

## Usage

These manifests are applied in order by `make install-core`:

1. **Cilium** is installed via Helm using `cilium-values.yaml`
2. **kube-proxy** is deleted (Cilium replaces it)
3. **Cilium IP Pool** is applied for LoadBalancer services
4. **Cert-Manager** is installed for ACME TLS certificate management

After ArgoCD is installed (`make install-argo`), these components can be managed declaratively via GitOps.
