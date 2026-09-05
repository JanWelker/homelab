---
description: "Running the cluster day to day: health checks, rebooting nodes, and recovering from the failures that actually happen."
---

# Operations

Everything on this page assumes a cluster that is already up. For first
provisioning see the [Quickstart](../quickstart.md).

## Routine health check

```bash
kubectl get nodes
kubectl -n argocd get applications
kubectl get certificate -A
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status
```

The Rook toolbox pod is enabled, so `ceph status`, `ceph osd tree` and
`ceph health detail` are available without installing anything.

!!! note
    Ceph metrics are **not** scraped: `monitoring.enabled` is `false` in the
    `CephCluster` spec, so storage problems show up in `ceph status` and the
    [Rook dashboard](../platform/rook-ceph.md) but never in Grafana or
    Prometheus.

## After any node reboot: unseal OpenBao

This is the single most common way the cluster comes back "up" but broken.
OpenBao seals itself whenever its pods restart, and while it is sealed no
`ExternalSecret` resolves — which means cert-manager cannot renew certificates.

Follow [OpenBao &rarr; Unsealing after a restart](../platform/openbao.md#unsealing-after-a-restart).
You need 3 of the 5 unseal keys.

## Rebooting a node

Nodes do **not** reboot themselves (see [OS updates](upgrades.md#os-updates)),
so this is a manual, one-node-at-a-time procedure.

```bash
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
ssh core@<node> sudo systemctl reboot
# wait for the node to come back
kubectl uncordon <node>
```

Between each node, confirm Ceph has recovered before moving on — draining a
second node while the first is still backfilling can take a placement group
below its minimum replica count:

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status   # HEALTH_OK
```

If the rebooted node hosted an OpenBao replica, unseal it afterwards.

## Rolling back a bad sync

Everything under `payload/` is applied by ArgoCD from Git, so the durable fix is
a revert commit. To stop the bleeding first, disable auto-sync on the affected
Application and roll it back:

```bash
kubectl -n argocd patch application <app> --type merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}'
argocd app rollback <app>
```

Re-enable auto-sync by restoring `syncPolicy.automated` once the revert has
landed on `main`. Leaving it disabled means the Application silently stops
tracking Git.

!!! warning
    Do not fix a broken workload by editing live objects with `kubectl edit`.
    Every Application here runs with `selfHeal: true`, so ArgoCD reverts the
    change within minutes and the real cause gets harder to find.

## Adding a node after the initial build

The bootstrap token generated at provisioning time has a 24 hour TTL, so it has
long expired on an established cluster. Generate a fresh join command on a
control-plane node:

```bash
ssh core@<control-plane-node>
sudo kubeadm token create --print-join-command
```

For a new **control-plane** node you also need a current certificate key, which
expires after two hours:

```bash
sudo kubeadm init phase upload-certs --upload-certs
```

Add the host to `ansible/inventory.yaml`, re-run `make config` and `make serve`
so it can PXE boot, then run the printed join command on it. The
`bootstrap-k8s.service` unit only fires when `/etc/kubernetes/kubelet.conf` is
absent, so it will not interfere with a node that has already joined.

## Replacing a failed node

1. Remove it from the cluster:

    ```bash
    kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force
    kubectl delete node <node>
    ```

2. Let Ceph re-replicate. With `useAllNodes: true` the OSD on that disk is gone
   for good; check `ceph status` returns to `HEALTH_OK` before continuing.
3. If it was a control-plane node, remove its etcd member:

    ```bash
    kubectl -n kube-system exec -it etcd-<healthy-node> -- etcdctl \
      --cacert /etc/kubernetes/pki/etcd/ca.crt \
      --cert /etc/kubernetes/pki/etcd/server.crt \
      --key /etc/kubernetes/pki/etcd/server.key \
      member list
    # then: member remove <id>
    ```

4. Reprovision the replacement following [Adding a node](#adding-a-node-after-the-initial-build).

!!! danger
    On a cluster provisioned before the [Control Plane VIP](control-plane-vip.md),
    `odin` is not an interchangeable control-plane node: its address is baked in
    as the API endpoint and as Cilium's `k8sServiceHost`, so losing it breaks
    node joins and Cilium's API connection on every other node. Check which
    endpoint your kubeconfig uses before assuming otherwise.

## Where to look when something is wrong

| Symptom | First check |
| --- | --- |
| Secrets missing, certificates not renewing | OpenBao sealed — `bao status` |
| Pods pending on a fresh node | Node still `NotReady`; check Cilium is running on it |
| `ExternalSecret` not syncing | [External Secrets troubleshooting](../platform/external-secrets.md#troubleshooting) |
| PVCs stuck pending | `ceph status`, then the [Rook dashboard](../platform/rook-ceph.md) |
| A hostname stopped resolving to the cluster | Gateway lost its LoadBalancer IP; check the Cilium L2 pool |
| Node not rejoining after reboot | `journalctl -u kubelet` on the node |

Alerts are mailed by Alertmanager — see
[Monitoring &rarr; Alerting](../platform/monitoring.md#alerting). The checks above
are still worth running, because the delivery path itself is not monitored.
