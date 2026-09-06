---
description: "Running the cluster day to day: health checks, rebooting nodes, and recovering from the failures that actually happen."
---

# Operations

Everything on this page assumes a cluster that is already up. For first
provisioning see the [Quickstart](../quickstart.md).

Building a cluster is a weekend. Operating one is the rest of your life. This is
the page you will actually come back to.

## Routine health check

Four commands, thirty seconds, run them when you walk past the rack:

```bash
kubectl get nodes
kubectl -n argocd get applications
kubectl get certificate -A
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status
```

The Rook toolbox pod is enabled, so `ceph status`, `ceph osd tree` and
`ceph health detail` are available without installing anything.

!!! note
    Ceph metrics **are** scraped — `monitoring.enabled` is `true` in the `CephCluster` spec, and `createPrometheusRules` ships Ceph's own alerting rules — so a degraded pool or a down OSD reaches Prometheus without anyone running `ceph status`. Run it anyway: it is the fastest way to see *why*, and it is what [Kured](../platform/kured.md) is really asking about before it reboots anything. See [Rook-Ceph &rarr; Monitoring](../platform/rook-ceph.md#monitoring).

## After any node reboot: check OpenBao came back unsealed

OpenBao seals itself whenever its pods restart, and while it is sealed no
`ExternalSecret` resolves — which means cert-manager cannot renew certificates.
[Auto-unseal](../platform/openbao.md#auto-unseal) normally handles this on its
own, so this is a check rather than a chore:

```bash
kubectl -n openbao get pods          # all three Ready
kubectl -n openbao exec -it openbao-0 -- bao status   # Sealed: false
```

It is worth actually running. A cluster that comes back with OpenBao still
sealed looks entirely healthy — every pod green, every node `Ready` — and the
consequence surfaces sixty days later when a certificate expires on a Sunday,
with nothing connecting it to the reboot that caused it.

If the pods did stay sealed, AWS KMS was unreachable when they started. Unseal
by hand with 3 of the 5 recovery keys per
[OpenBao &rarr; Unsealing after a restart](../platform/openbao.md#unsealing-after-a-restart),
then find out why KMS could not be reached — see
[the limitation this creates](../architecture/limitations.md#openbao-depends-on-aws-kms-to-start).

## Rebooting a node

[Kured](../platform/kured.md) reboots nodes on its own between 01:00 and 05:00
when an update has been staged, one at a time, and refuses while Ceph or etcd is
unhealthy. The procedure below is for the cases it does not cover: rebooting
sooner than the window, or rebooting a node for a reason nothing set a sentinel
for.

```bash
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
ssh core@<node> sudo systemctl reboot
# wait for the node to come back
kubectl uncordon <node>
```

Between each node, confirm Ceph has recovered before moving on — draining a
second node while the first is still backfilling can take a placement group
below its minimum replica count. Ceph is patient; impatient operators are how
"one node down" becomes "read-only cluster":

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status   # HEALTH_OK
```

If the rebooted node hosted an OpenBao replica, confirm it came back unsealed.

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
tracking Git — and an Application that has quietly stopped tracking Git is a
time bomb with a three-month fuse, defused only by somebody wondering why their
change never took effect.

!!! warning
    Do not fix a broken workload by editing live objects with `kubectl edit`. Every Application here runs with `selfHeal: true`, so ArgoCD reverts the change within minutes and the real cause gets harder to find — and you will spend twenty minutes convinced you are losing your mind before you remember why.

## Adding a node after the initial build

The bootstrap token generated at provisioning time has a 24 hour TTL, so it has
long expired on an established cluster. This trips up everyone exactly once.
Generate a fresh join command on a control-plane node:

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
   for good; check `ceph status` returns to `HEALTH_OK` before continuing. This
   is not a step to rush — Ceph will tell you when it is done, and it is never as
   fast as you would like.
3. If it was a control-plane node, remove its etcd member. A dead member left in
   the list still counts toward quorum, which is a delightful way to lose a
   cluster that is otherwise fine:

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
    On a cluster provisioned before the [Control Plane VIP](control-plane-vip.md), `odin` is not an interchangeable control-plane node: its address is baked in as the API endpoint and as Cilium's `k8sServiceHost`, so losing it breaks node joins and Cilium's API connection on every other node. Check which endpoint your kubeconfig uses before assuming otherwise.

## Where to look when something is wrong

The table that saves the most time. Resist the urge to start with `kubectl
describe` on the thing that looks broken; start here, because the thing that
looks broken is usually downstream of something duller:

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
are still worth running, because the delivery path itself is not monitored. An
empty inbox means either that nothing is wrong or that the mail stopped working,
and those two look identical from here.
