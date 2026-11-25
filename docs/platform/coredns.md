---
description: "Cluster DNS: kubeadm deploys CoreDNS at init, Argo CD owns it afterwards, and kubeadm upgrades leave it alone."
---

# CoreDNS

[CoreDNS](https://coredns.io/) answers every pod's DNS lookups on the
`kube-dns` Service. `kubeadm init` deploys it, because Argo CD cannot clone the
repository before names resolve; the `coredns` Application then adopts the same
objects by name, and every `kubeadm upgrade` here skips `addon/coredns`, so the
manifests in Git are the only source of its spec.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` |
| Depends on | Nothing; the nodes' `/etc/resolv.conf` for upstream names (`dnsPolicy: Default`) |
| If it is down | Every in-cluster name lookup, so nearly every workload |
| Health check | `kubectl -n kube-system get deploy coredns` &rarr; `3/3` on three different nodes |
| Files | [`payload/platform/coredns/`](https://github.com/JanWelker/homelab/tree/main/payload/platform/coredns) |

## Configuration

| Setting | Why |
| --- | --- |
| Object names, labels and the selector as kubeadm creates them | The adoption patches the live objects instead of replacing them; a changed selector would recreate the Deployment |
| `clusterIP` of `kube-dns` | The kubelets' `clusterDNS`, the tenth address of `service_subnet` |
| Three replicas, one per node, workers preferred | A drain plus a node failure still leaves a resolver; control planes only when no worker fits |
| `Delete=false` on every object | Deleting the Application keeps DNS |
| Human-merged image digest | See [Pitfalls](#pitfalls) |

## Usage

Change the Corefile in `configmap.yaml`; the `reload` plugin picks it up within
a minute without a restart.

## Health check

```bash
kubectl -n kube-system get pods -l k8s-app=kube-dns -o wide
kubectl -n kube-system get endpointslices -l kubernetes.io/service-name=kube-dns   # three ready addresses
```

## Pitfalls

- **A CoreDNS bump may need a Corefile change.** kubeadm migrated the Corefile
  between releases; now the Renovate PR waits for a human, who reads the
  release notes for removed or renamed plugins first.
- **`kubeadm upgrade` without `addon/coredns` in `--skip-phases`** rewrites the
  Deployment to kubeadm's spec; Argo CD's self-heal reverts it, but the pods
  roll twice. `make kube-upgrade` and the
  [minor runbook](../operations/upgrades.md#upgrading-a-minor-version-deliberately)
  pass it.
