# GitOps Strategy

We use **ArgoCD** to manage the cluster state declaratively.

## App-of-Apps Pattern

We use a hierarchical Application structure to manage dependencies and logical grouping.

```mermaid
flowchart LR
    subgraph "Bootstrap (Manual)"
        RA[root.yaml]
    end

    subgraph "Parent Applications"
        RA --> WL[workloads]
        RA --> PL[platform]
        RA --> GO[gitops]
    end

    subgraph "Managed by workloads"
        WL --> |"payload/workloads/**/application.yaml"| APPS[User Apps]
    end

    subgraph "Managed by platform"
        PL --> |"payload/platform/**"| INFRA[Core Components]
    end

    subgraph "Managed by gitops"
        GO --> |"payload/argocd/*"| ARGO[ArgoCD Self-Management]
    end
```

## Deployment Waves

ArgoCD uses **sync waves** to control deployment order. Lower waves sync first.
This ensures CRDs exist before Operators, and Storage exists before
Applications.

```mermaid
flowchart TB
    subgraph "Wave -10: CRDs"
        GW[gateway-api-crds]
    end

    subgraph "Wave -5: Security"
        CM[cert-manager]
    end

    subgraph "Wave -2: Operators"
        RO[rook-ceph-operator]
    end

    subgraph "Wave -1: Infrastructure"
        CL[cilium]
        RC[rook-ceph-cluster]
    end

    subgraph "Wave 0: Core Apps"
        AR[argocd]
        K8[k8up]
    end

    subgraph "Wave 1+: User Apps"
        MON[kube-prometheus-stack]
        NC[nextcloud]
        HA[home-assistant]
        ARC[arc-runner-set]
    end

    %% Dependencies
    GW --> CL
    CM --> RC
    RO --> RC
    CL --> AR
    RC --> NC
    RC --> HA
    AR --> ARC
```
