---
description: "Rebooting, adding, replacing and rebuilding nodes, in the order the steps have to happen."
---

# Node Lifecycle

Everything you do to one machine, and the cluster's way back after a power
loss.

!!! danger "Two of these destroy data and two do not"
    [Rebooting](#rebooting-a-node) and [adding](#adding-a-node) are safe and routine. [Replacing](#replacing-a-failed-node) destroys one node's OSD. [Rebuilding](#rebuilding-or-repartitioning-a-node) destroys the disk it runs against, and doing it to every node in turn destroys every replica of everything.

## Rebooting a node

[Kured](../platform/kured.md) does every reboot: its lock keeps one node down
at a time, and it waits while a Ceph, etcd, node or API alert fires. A hand
reboot skips both, so it is kept for a node being wiped or dead. Flatcar is
installed to disk, so the node boots without the boot server and keeps
`/etc/kubernetes`, `/var/lib/etcd` and `/var/lib/rook`. A change made with
`make config` is *not* picked up by a reboot; that takes a
[reprovision](#reprovisioning-a-node).

1. Set the sentinel; Kured acts on it at its next `period` check.

    ```bash
    make reboot-all LIMIT=<node>
    ```

2. Watch it cordon, reboot and uncordon the node.

    ```bash
    kubectl get node <node> -w
    kubectl -n kured logs -l app.kubernetes.io/name=kured --tail=50 -f
    ```

    Done when the node is `Ready` without `SchedulingDisabled`. Never
    cordoned: the log names the alert or lock holding it; fix that instead of
    rebooting by hand. A drain failing on a `<name>-db-<n>` pod's disruption
    budget is a CloudNativePG cluster with its budget still on — see
    [the contract](../platform/cloudnative-pg.md#the-contract).

3. **Unseal OpenBao** if the node hosted a replica (below).

## After any node reboot: unseal OpenBao

OpenBao comes back sealed, looks healthy while sealed, and nothing unseals
it for you — see [OpenBao](../platform/openbao.md) for what that costs.

```bash
make bao-unseal                      # idempotent: unseals whatever is sealed
for pod in openbao-0 openbao-1 openbao-2; do
  kubectl -n openbao exec "$pod" -- bao status | grep Sealed   # false
done
```

With `openbao-init.json` moved to a password manager, unseal
[by hand](../platform/openbao.md#unsealing-after-a-restart).

## After a power loss

The nodes boot network-first, so the PXE menus decide every boot.

1. Before starting the boot server, check every menu says `localboot`: an
   armed node wipes its disk on this boot. Disarm anything else.

    ```bash
    grep -H '^DEFAULT' output/tftp/pxelinux.cfg/01-*
    make reinstall-cancel    # if any says install
    ```

2. Wait for the control planes, then the workers, to report `Ready`.

    ```bash
    kubectl get nodes -w
    ```

3. Wait for Ceph's `HEALTH_OK`.

    ```bash
    kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status
    ```

4. [Unseal OpenBao](#after-any-node-reboot-unseal-openbao), every replica.

    ```bash
    make bao-unseal
    ```

5. Wait for every Application to be `Synced` and `Healthy`; failed syncs
   [retry](../architecture/gitops.md#sync-policy).

    ```bash
    kubectl -n argocd get applications
    ```

## Adding a node

1. Compare `kubernetes_version` in `ansible/inventory.yaml` with
   `kubectl version`: `kubeadm join` refuses a kubelet newer than the API
   server, so a merged minor is
   [walked onto the cluster](upgrades.md#upgrading-a-minor-version-deliberately)
   first. Then add the host and regenerate and serve the boot files.

    ```bash
    make config && make serve
    ```

    In the same commit, add the node to `providerRegex` in
    `payload/platform/kubelet-csr-approver/application.yaml` and let it sync
    first, or its kubelet certificate is denied and it
    [drops out of `kubectl top`](../platform/metrics-server.md#pitfalls).

    Against a running cluster, `make config` also
    [renews the join credentials](../architecture/ansible.md#playbooks) for
    a limited window (`ansible/templates/renew_join_credentials.sh.j2`).

2. Arm the node: the generated menu defaults to the local disk, so an unarmed
   node never installs, per
   [Switching back to local boot](../architecture/boot-process.md#switching-back-to-local-boot).

    ```bash
    make reinstall LIMIT=<node>
    ```

3. Network-boot it within that window, shorter for a **control-plane** node.
   `bootstrap-k8s.service` joins only while `/etc/kubernetes/kubelet.conf`
   is absent.

4. If the window has passed, run `make config` again, then retry the join on
   the node.

    ```bash
    ssh core@<node> sudo systemctl restart bootstrap-k8s
    ```

## Replacing a failed node

1. Remove it from the cluster. A dead node is the exception to
   [Kured](#rebooting-a-node): there is nothing left to reboot.

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

4. Reprovision the replacement per [Adding a node](#adding-a-node): the
   inventory and regex change, then arm, then boot.

## Configuration drift

Ignition runs once, so a template change reaches a running node only through a
[reprovision](#reprovisioning-a-node), never a hand edit. `make node-drift`
compares the checksum each node config writes to `/etc/node-config.sha256`
with a fresh render.

## Reprovisioning a node

`flatcar-reset` deletes the root filesystem except the state listed in
`ansible/playbooks/vars/reprovision.yaml`, and Ignition writes the rest from
the node's `-reprovision` config, which leaves out the disks and the bootstrap
files: the node keeps its OSD and rejoins as itself, without the boot server —
see [Design Decisions](../architecture/decisions.md#flatcar-reset-not-in-place-sync).
On a control plane, `kubeadm-reconcile.service` then re-renders the API
server, controller manager and scheduler from the template at the version the
node runs, and the first control plane uploads the `kubeadm-config` that
`kubeadm upgrade` reads; etcd's manifest is kept as it is.

1. Stage it; the playbook refuses a minor the API server does not run, and a
   keep list that misses the node's etcd, kubelet or kubeadm state.
   `flatcar-reset --preview-keep` and `--backup` print and copy nothing on
   these nodes, since they cannot unmount `/etc`.

    ```bash
    make reprovision LIMIT=<node>
    ```

2. Let [Kured](#rebooting-a-node) reboot it, one node at a time. It must come
   back `Ready` and `current` in `make node-drift`; stop otherwise.

## Rebuilding or repartitioning a node

The disk layout is applied by Ignition at install only;
[reprovisioning](#reprovisioning-a-node) leaves it alone. `rook-osd` is the last partition and deliberately raw:
move its start by a sector, which any insert or resize above it does, and every
OSD on that node is gone. Only the last partition can grow without a reinstall,
and `rook-osd` cannot shrink in place because Ceph has already written across
the space.

!!! danger "The backups are inside the thing being wiped"
    Every backup CronJob and Velero write to the Ceph object store this destroys — see [Backups & Recovery](backups.md#what-is-not-covered). Copy anything you intend to restore from **off-cluster** first.

1. Confirm the [off-cluster copy](backups.md#off-cluster-copy) ran after the
   last nightly backup, and copy anything in a PVC not reproducible from Git.
2. Edit whatever needs editing under `ansible/`.
3. Regenerate both Ignition configs and serve them.

    ```bash
    make config && make serve
    ```

4. Drain the node by hand, not through [Kured](#rebooting-a-node). A
   **control-plane** node also leaves the cluster (steps 1 and 3 of
   [Replacing a failed node](#replacing-a-failed-node)), or it returns with
   an empty etcd kubeadm treats as joined.

    ```bash
    kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
    ```

5. Arm the nodes you are rebuilding; the boot server disarms each once it
   has the image — see
   [Switching back to local boot](../architecture/boot-process.md#switching-back-to-local-boot).

    ```bash
    make reinstall LIMIT=odin   # `make reinstall` alone asks, then arms every host
    ```

    Only [`bootstrap_cluster`](../architecture/ansible.md#inventory) renders
    `kubeadm init`; arming refuses it while the VIP answers.

6. Network-boot the node. The installer wipes the disk, runs `flatcar-install`,
   and reboots into the installed system, which then runs `kubeadm`.
7. Leave the boot server up until the node is `Ready` (the sysext images are
   fetched from it on that first boot), then stop it.
8. Wait for `ceph status` to report `HEALTH_OK` before the next node: the
   OSD is new and backfills from the other two copies, and
   [`make storage-check`](../platform/rook-ceph.md#is-storage-ready) proves
   it took the disk.
9. [Unseal OpenBao](#after-any-node-reboot-unseal-openbao) if a replica
   moved.
