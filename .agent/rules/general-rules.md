---
trigger: always_on
---

1. Deployments via ArgoCD Gitops controlled by payload/root.yaml
2. Documentation location: payload/workloads/documentation/src/docs/
3. Ingress/httpRoute template dir: payload/workloads/nginx-test
4. Always use the latest stable version for external components
5. MCP to ArgoCD is available
6. kubectl via cli is available
