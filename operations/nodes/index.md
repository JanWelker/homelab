# Node Lifecycle

Everything you do to one machine, and the cluster's way back after a power loss.

> [!CAUTION]
>
> **Two of these destroy data and two do not**
>
> [Rebooting](<#rebooting-a-node>) and [adding](<#adding-a-node>) are safe and routine. [Replacing](<#replacing-a-failed-node>) destroys one node's OSD. [Rebuilding](<#rebuilding-or-repartitioning-a-node>) destroys the disk it runs against, and doing it to every node in turn destroys every replica of everything.

## Rebooting a node

[Kured](<https://homelab.wlkr.ch/platform/kured/index.md>) does every reboot: its lock keeps one node down at a time, and it waits while a Ceph, etcd, node or API alert fires. A hand reboot skips both, so it is kept for a node being wiped or dead. Flatcar is installed to disk, so the node boots without the boot server and keeps `/etc/kubernetes`, `/var/lib/etcd` and `/var/lib/rook`. A change made with `make config` is *not* picked up by a reboot; that takes a [rebuild](<#rebuilding-or-repartitioning-a-node>).

1. Set the sentinel; Kured acts on it at its next `period` check.

   ```bash
   make reboot-all LIMIT=<node>
   ```
2. Watch it cordon, reboot and uncordon the node.

   ```bash
   kubectl get node <node> -w
   kubectl -n kured logs -l app.kubernetes.io/name=kured --tail=50 -f
   ```

   Done when the node is `Ready` without `SchedulingDisabled`. Never cordoned: the log names the alert or lock holding it; fix that instead of rebooting by hand. A drain failing on a `<name>-db-<n>` pod's disruption budget is a CloudNativePG cluster with its budget still on — see [the contract](<https://homelab.wlkr.ch/platform/cloudnative-pg/index.md#the-contract>).
3. **Unseal OpenBao** if the node hosted a replica (below).

## After any node reboot: unseal OpenBao

OpenBao comes back sealed, looks healthy while sealed, and nothing unseals it for you — see [OpenBao](<https://homelab.wlkr.ch/platform/openbao/index.md>) for what that costs.

```bash
make bao-unseal                      # idempotent: unseals whatever is sealed
for pod in openbao-0 openbao-1 openbao-2; do
  kubectl -n openbao exec "$pod" -- bao status | grep Sealed   # false
done
```

With `openbao-init.json` moved to a password manager, unseal [by hand](<https://homelab.wlkr.ch/platform/openbao/index.md#unsealing-after-a-restart>).

## After a power loss

The nodes boot network-first, so the PXE menus decide every boot.

1. Before starting the boot server, check every menu says `localboot`: an armed node wipes its disk on this boot. Disarm anything else.

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
4. [Unseal OpenBao](<#after-any-node-reboot-unseal-openbao>), every replica.

   ```bash
   make bao-unseal
   ```
5. Wait for every Application to be `Synced` and `Healthy`; failed syncs [retry](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>).

   ```bash
   kubectl -n argocd get applications
   ```

## Adding a node

1. Compare `kubernetes_version` in `ansible/inventory.yaml` with `kubectl version`: a node installs the inventory's version, and `kubeadm    join` refuses a kubelet newer than the API server, so a merged minor has to be [walked onto the cluster](<https://homelab.wlkr.ch/operations/upgrades/index.md#upgrading-a-minor-version-deliberately>) first. Then add the host to the inventory and regenerate and serve the boot files.

   ```bash
   make config && make serve
   ```

   In the same commit, add the node name to `providerRegex` in `payload/platform/kubelet-csr-approver/application.yaml`, and let that PR merge and sync before the node boots: a node the regex does not name has its kubelet certificate denied and [silently drops out of `kubectl top`](<https://homelab.wlkr.ch/platform/metrics-server/index.md#pitfalls>).

   Against a running cluster, `make config` also re-creates the bootstrap token baked into the Ignition configs (`ttl` in `ansible/templates/renew_join_credentials.sh.j2`) and re-uploads the control-plane certificates for kubeadm's shorter fixed window, on [whichever control plane answers](<https://homelab.wlkr.ch/architecture/ansible/index.md#playbooks>), so the node joins on its own.
2. Arm the node: the generated menu defaults to the local disk, so an unarmed node never installs, per [Switching back to local boot](<https://homelab.wlkr.ch/architecture/boot-process/index.md#switching-back-to-local-boot>).

   ```bash
   make reinstall LIMIT=<node>
   ```
3. Network-boot it within those windows, the shorter one for a **control-plane** node. `bootstrap-k8s.service` runs the join once and only while `/etc/kubernetes/kubelet.conf` is absent, so it does not interfere with a node that has joined.
4. If the window has passed, run `make config` again, then retry the join on the node.

   ```bash
   ssh core@<node> sudo systemctl restart bootstrap-k8s
   ```

## Replacing a failed node

1. Remove it from the cluster. A dead node is the exception to [Kured](<#rebooting-a-node>): there is nothing left to reboot.

   ```bash
   kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --force
   kubectl delete node <node>
   ```
2. Let Ceph re-replicate. With `useAllNodes: true` the OSD on that disk is gone for good; wait for `ceph status` to return to `HEALTH_OK`.
3. If it was a control-plane node, remove its etcd member; a dead member still counts toward quorum.

   ```bash
   kubectl -n kube-system exec -it etcd-<healthy-node> -- etcdctl \
     --cacert /etc/kubernetes/pki/etcd/ca.crt \
     --cert /etc/kubernetes/pki/etcd/server.crt \
     --key /etc/kubernetes/pki/etcd/server.key \
     member list
   # then: member remove <id>
   ```
4. Reprovision the replacement per [Adding a node](<#adding-a-node>): the inventory and regex change, then arm, then boot.

> [!CAUTION]
>
> **Danger**
>
> On a cluster provisioned before the [Control Plane VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>), `odin` is not an interchangeable control-plane node: its address is baked in as the API endpoint and as Cilium's `k8sServiceHost`, so losing it breaks node joins and Cilium's API connection everywhere. Check which endpoint your kubeconfig uses first.

## Rebuilding or repartitioning a node

The disk layout in `ansible/templates/butane_node_config.yaml.j2` is applied by Ignition once, on the first boot after install, and the Ignition config is embedded in the OEM partition at install time, so no change under `ansible/` reaches a running node. `rook-osd` is the last partition and deliberately raw: move its start by a sector, which any insert or resize above it does, and every OSD on that node is gone. Only the last partition can grow without a reinstall, and `rook-osd` cannot shrink in place because Ceph has already written across the space.

> [!CAUTION]
>
> **The backups are inside the thing being wiped**
>
> Every backup CronJob and Velero write to the Ceph object store this destroys — see [Backups & Recovery](<https://homelab.wlkr.ch/operations/backups/index.md#what-is-not-covered>). Copy anything you intend to restore from **off-cluster** first.

1. Confirm the [off-cluster copy](<https://homelab.wlkr.ch/operations/backups/index.md#off-cluster-copy>) ran after the last nightly backup, and copy anything in a PVC not reproducible from Git.
2. Edit whatever needs editing under `ansible/`.
3. Regenerate both Ignition configs and serve them.

   ```bash
   make config && make serve
   ```
4. Drain the node by hand: [Kured](<#rebooting-a-node>) would reboot it into the installer before it has left the cluster. A **control-plane** node also leaves the cluster (steps 1 and 3 of [Replacing a failed node](<#replacing-a-failed-node>)): it comes back with an empty etcd under its old peer URL, which kubeadm treats as joined.

   ```bash
   kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
   ```
5. Arm the nodes you are rebuilding. Arming is the only way in (the menu shows no prompt), a later `make config` keeps a node armed, and the boot server disarms it once it has the image, so the reboot at the end of the install does not start a second one — see [Switching back to local boot](<https://homelab.wlkr.ch/architecture/boot-process/index.md#switching-back-to-local-boot>).

   ```bash
   make reinstall LIMIT=odin   # `make reinstall` alone asks, then arms every host
   ```

   Every control plane joins the running cluster; only [`bootstrap_cluster`](<https://homelab.wlkr.ch/architecture/ansible/index.md#inventory>) renders `kubeadm init`, and arming refuses while it is set and the VIP answers.
6. Network-boot the node. The installer wipes the disk, runs `flatcar-install`, and reboots into the installed system, which then runs `kubeadm`. The node keeps the SSH host key `make config` generated, so `output/known_hosts` still matches.
7. Leave the boot server up until the node is `Ready` (the sysext images are fetched from it on that first boot), then stop it.
8. Wait for `ceph status` to report `HEALTH_OK` before the next node: the OSD is new and backfills from the other two copies, and [`make storage-check`](<https://homelab.wlkr.ch/platform/rook-ceph/index.md#is-storage-ready>) proves it took the disk.
9. [Unseal OpenBao](<#after-any-node-reboot-unseal-openbao>) if a replica moved.
