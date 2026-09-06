---
description: "The App-of-Apps pattern, parent ArgoCD Applications, and the sync waves that order cluster deployment."
---

# GitOps Strategy

**ArgoCD** manages the cluster state declaratively. The rule is simple and
absolute: if it is not in Git, it is not in the cluster — and if you put it in
the cluster anyway, `selfHeal` will remove it while you are still admiring your
work.

This is not pedantry. It is the difference between a cluster you can rebuild
from a repository and a cluster held together by a series of `kubectl apply`
commands that exist only in one person's shell history.

## App-of-Apps Pattern

A hierarchical Application structure manages dependencies and logical grouping.
One `kubectl apply` of `payload/root.yaml` bootstraps everything else; from
there the repository discovers itself.

```mermaid
flowchart LR
    subgraph "Bootstrap (Manual)"
        RA[root.yaml]
    end

    subgraph "Parent Applications"
        RA --> PL[platform]
        RA --> GO[gitops]
    end

    subgraph "Managed by platform"
        PL --> |"payload/platform/**"| INFRA[Core Components]
    end

    subgraph "Managed by gitops"
        GO --> |"payload/argocd/*"| ARGO[ArgoCD Self-Management]
    end
```

Yes, ArgoCD manages ArgoCD. It is exactly as recursive as it sounds, and it
works fine until the day you sync a broken ArgoCD config with ArgoCD. Keep
`make install-argo` in your back pocket for that day.

## Deployment Waves

ArgoCD uses **sync waves** to control deployment order. Lower waves sync first.
This ensures CRDs exist before Operators, and Storage exists before
Applications.

Sync waves are the answer to the question "why did my perfectly correct manifest
fail on a fresh cluster and work on an existing one?" On a running cluster
everything it depends on already exists. On a fresh one, ordering is the whole
game.

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
    end

    subgraph "Wave 1+: User Apps"
        MON[kube-prometheus-stack]
    end

    %% Dependencies
    GW --> CL
    CM --> RC
    RO --> RC
    CL --> AR
    AR --> MON
```

The full wave-by-wave listing is in [Platform &rarr; Usage](../platform/index.md#usage).
