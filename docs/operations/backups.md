---
description: "What is backed up, what is not, and how to snapshot etcd and OpenBao before you need them."
---

# Backups & Recovery

## Current state

| Data | Backed up | How |
| --- | --- | --- |
| OpenBao secrets | Manual | [Raft snapshot](../platform/openbao.md#backups) |
| OpenBao unseal keys | Manual, off-cluster | Printed once at `bao operator init` |
| etcd (all Kubernetes objects) | **No** | Procedure below, not automated |
| Ceph RBD volumes (PVCs) | **No** | — |
| Grafana dashboards | **No** | Live on a Ceph PVC |
| Prometheus metrics | **No** | 10 day retention, then gone |
| Everything in `payload/` | Yes | It is in Git; that is the point of GitOps |

The cluster is reconstructible from Git and the OpenBao unseal keys. Anything
written *into* the cluster — dashboards created in the Grafana UI, data in a
workload's PVC — is not.

## What is not covered

Nothing on this page is automated, and **nothing alerts when it is missed**.
Alertmanager is deployed with storage but no route and no receiver
(`payload/platform/monitoring/application.yaml`), so alerts fire into the
default null receiver and nobody is notified. Ceph metrics are not scraped at
all. Treat the checks in [Operations](index.md#routine-health-check) as a manual
substitute until a receiver is configured.

## etcd

etcd holds every Kubernetes object. Losing it without a snapshot means
rebuilding the cluster and re-syncing from Git — which recovers `payload/` but
not the objects created outside it, including cert-manager's issued
certificates and any `Secret` materialised by ESO.

Take a snapshot from a control-plane node:

```bash
kubectl -n kube-system exec -it etcd-<node> -- etcdctl \
  --cacert /etc/kubernetes/pki/etcd/ca.crt \
  --cert /etc/kubernetes/pki/etcd/server.crt \
  --key /etc/kubernetes/pki/etcd/server.key \
  snapshot save /var/lib/etcd/snapshot.db

kubectl cp kube-system/etcd-<node>:/var/lib/etcd/snapshot.db ./etcd-snapshot.db
```

Copy it off the cluster. A snapshot stored only on a Ceph PVC does not survive
the failure it exists for.

Restoring is `etcdctl snapshot restore` into a fresh data directory on a stopped
control plane, then restarting kubelet — see the
[upstream kubeadm documentation](https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/#restoring-an-etcd-cluster).

## OpenBao

```bash
kubectl -n openbao exec -it openbao-0 -- bao operator raft snapshot save /tmp/snapshot.bao
kubectl -n openbao cp openbao-0:/tmp/snapshot.bao ./openbao-snapshot.bao
```

The snapshot contains all KV data plus policies, roles and mounts. It does
**not** contain the unseal keys, and it is useless without them. Details in
[OpenBao &rarr; Backups](../platform/openbao.md#backups).

## Ceph volumes

There is no volume backup. Rook-Ceph replicates across OSDs, which protects
against a disk or node failure but not against deletion, corruption, or a bad
`prune`. Every Application here runs with `prune: true`, so removing a
`PersistentVolumeClaim` from Git deletes the volume.

Options, none currently implemented: enable RBD mirroring to a second cluster,
run [Velero](https://velero.io/) with the CSI snapshot plugin, or back up at the
application layer.

## Rebuilding from scratch

What you need, in order:

1. The Git repository — all platform and workload manifests.
2. The OpenBao unseal keys and root token — without these the restored OpenBao
   is an encrypted brick.
3. An OpenBao raft snapshot, or the willingness to re-enter every secret.
4. Optionally an etcd snapshot, to skip re-issuing certificates and waiting for
   the platform to re-converge.

The rebuild itself is the [Quickstart](../quickstart.md) from step 1. Because
the platform is declarative, the cluster converges back to its documented state
once ArgoCD points at the repository and OpenBao is unsealed.
