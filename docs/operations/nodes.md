---
description: "Rebooting, adding, replacing and rebuilding nodes, in the order the steps have to happen."
---

# Node Lifecycle

Everything you do to one machine. The four procedures share a shape: drain,
act, wait for Ceph, move on.

!!! danger "Two of these destroy data and two do not"
    [Rebooting](#rebooting-a-node) and [adding](#adding-a-node) are safe and routine. [Replacing](#replacing-a-failed-node) destroys one node's OSD. [Rebuilding](#rebuilding-or-repartitioning-a-node) destroys the disk it runs against, and doing it to every node in turn destroys every replica of everything.

## Rebooting a node

[Kured](../platform/kured.md) reboots nodes on its own once an update is
staged, one at a time, and refuses while Ceph or etcd is unhealthy. The
procedure below is for rebooting ahead of its next check (`period` in
`payload/platform/kured/application.yaml`), or for a
reason nothing set a sentinel for. Flatcar is installed to disk, so the node
boots without the boot server and keeps `/etc/kubernetes`, `/var/lib/etcd` and
`/var/lib/rook`. A change made with `make config` is *not* picked up by a
reboot; that takes a [rebuild](#rebuilding-or-repartitioning-a-node).

1. Drain it.

    ```bash
    kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
    ```

2. Reboot it and wait for `Ready`.

    ```bash
    ssh core@<node> sudo systemctl reboot
    kubectl get node <node> -w
    ```

3. Uncordon it.

    ```bash
    kubectl uncordon <node>
    ```

4. **Wait for Ceph** before touching the next node: draining a second node
   while the first is still backfilling can take a placement group below its
   minimum replica count.

    ```bash
    kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status   # HEALTH_OK
    ```

5. **Unseal OpenBao** if the node hosted a replica (below).

## After any node reboot: unseal OpenBao

OpenBao comes back sealed and looks healthy while sealed, and nothing unseals
it for you — see [OpenBao](../platform/openbao.md) for what that costs.

```bash
make bao-unseal                      # idempotent: unseals whatever is sealed
for pod in openbao-0 openbao-1 openbao-2; do
  kubectl -n openbao exec "$pod" -- bao status | grep Sealed   # false
done
```

`make bao-unseal` reads the key shares from
`output/credentials/openbao-init.json`. If that file has been moved into a
password manager, each pod wants 3 of the 5 shares by hand, per
[OpenBao](../platform/openbao.md).

## Adding a node

1. Add the host to `ansible/inventory.yaml`, then regenerate and serve the boot
   files.

    ```bash
    make config && make serve
    ```

    In the same commit, add the node name to `providerRegex` in
    `payload/platform/kubelet-csr-approver/application.yaml`: a node the regex
    does not name has its kubelet certificate denied and
    [silently drops out of `kubectl top`](../platform/metrics-server.md#pitfalls).

2. On a control-plane node, generate a fresh join command; the provisioning
   token has a 24 hour TTL and has long expired.

    ```bash
    ssh core@<control-plane-node>
    sudo kubeadm token create --print-join-command
    ```

3. For a new **control-plane** node, also upload a current certificate key,
   which expires after two hours.

    ```bash
    sudo kubeadm init phase upload-certs --upload-certs
    ```

4. Network-boot the new node and run the printed join command on it. The
   `bootstrap-k8s.service` unit only fires when `/etc/kubernetes/kubelet.conf`
   is absent, so it does not interfere with a node that has joined.

## Replacing a failed node

1. Remove it from the cluster.

    ```bash
    kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force
    kubectl delete node <node>
    ```

2. Let Ceph re-replicate. With `useAllNodes: true` the OSD on that disk is
   gone for good; wait for `ceph status` to return to `HEALTH_OK`.

3. If it was a control-plane node, remove its etcd member; a dead member still
   counts toward quorum.

    ```bash
    kubectl -n kube-system exec -it etcd-<healthy-node> -- etcdctl \
      --cacert /etc/kubernetes/pki/etcd/ca.crt \
      --cert /etc/kubernetes/pki/etcd/server.crt \
      --key /etc/kubernetes/pki/etcd/server.key \
      member list
    # then: member remove <id>
    ```

4. Reprovision the replacement per [Adding a node](#adding-a-node).

!!! danger
    On a cluster provisioned before the [Control Plane VIP](control-plane-vip.md), `odin` is not an interchangeable control-plane node: its address is baked in as the API endpoint and as Cilium's `k8sServiceHost`, so losing it breaks node joins and Cilium's API connection everywhere. Check which endpoint your kubeconfig uses first.

## Rebuilding or repartitioning a node

The disk layout in `ansible/templates/butane_node_config.yaml.j2` is applied by
Ignition once, on the first boot after install, and the Ignition config is
embedded in the OEM partition at install time, so no change under `ansible/`
reaches a running node. `rook-osd` is the last partition and deliberately raw:
move its start by a sector, which any insert or resize above it does, and every
OSD on that node is gone. Only the last partition can grow without a reinstall,
and `rook-osd` cannot shrink in place because Ceph has already written across
the space.

!!! danger "The backups are inside the thing being wiped"
    Velero and the etcd snapshot CronJob both write to the Ceph object store this destroys — see [Backups & Recovery](backups.md#what-is-not-covered). Copy anything you intend to restore from **off-cluster** first.

1. Copy what matters off the cluster: Velero backups, the latest etcd snapshot,
   and anything in a PVC not reproducible from Git.
2. Edit whatever needs editing under `ansible/`.
3. Regenerate both Ignition configs and serve them.

    ```bash
    make config && make serve
    ```

4. Arm the nodes you are rebuilding. Arming is the only way in (the menu shows
   no prompt), a later `make config` keeps a node armed, and the boot server
   disarms it once it has the image, so the reboot at the end of the install
   does not start a second one — see
   [Switching back to local boot](../architecture/boot-process.md#switching-back-to-local-boot).

    ```bash
    make reinstall LIMIT=odin   # `make reinstall` alone asks, then arms every host
    ```

5. Network-boot the node. The installer wipes the disk, runs `flatcar-install`,
   and reboots into the installed system, which then runs `kubeadm`. The node
   comes back with a new SSH host key; `make reinstall` already forgot the
   old one, or `StrictHostKeyChecking=accept-new` would refuse the first
   `make kubeconfig`.
6. Leave the boot server up until the node is `Ready` (the sysext images are
   fetched from it on that first boot), then stop it.

One node at a time is safe when rebuilding: etcd keeps quorum and Ceph
backfills, as in [Replacing a failed node](#replacing-a-failed-node).
Repartitioning destroys every OSD as it goes, so a rolling repartition across
all six nodes destroys all replicas of everything; step 1 is not optional for
that case.
