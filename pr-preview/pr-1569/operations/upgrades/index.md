# Updates &amp; Upgrades

Three mechanisms update a node, and all three land their changes on disk and wait for a reboot. A node can be four patch releases behind while reporting that everything is up to date.

## OS updates

Flatcar downloads OS updates in the background into the passive half of its A/B partition pair. `locksmithd`, which would normally coordinate the reboot, is masked in `ansible/templates/butane_node_config.yaml.j2`, because a reboot must drain the node first. In its place `flatcar-reboot-sentinel.timer` polls `update_engine_client -status` and touches `/run/reboot-required` once an update is staged. [Kured](<https://homelab.wlkr.ch/platform/kured/index.md>) consumes that marker: it drains, reboots and uncordons, one node at a time and only while Ceph and etcd are healthy. To move sooner, see [Rebooting a node](<https://homelab.wlkr.ch/operations/nodes/index.md#rebooting-a-node>).

Three alerts in `payload/platform/monitoring/node-update-rules.yaml` watch that this actually happens, because Trivy scans containers and not the host: `NodeRebootPending` when a node has carried the marker for longer than its `for`, `NodeOsVersionDrift` and `NodeKubeletVersionDrift` when the nodes have run different Flatcar or kubelet versions for longer than theirs.

```bash
ssh core@<node> 'cat /etc/os-release; systemctl status update-engine --no-pager'
```

## Kubernetes

Kubernetes is a [systemd sysext](<https://homelab.wlkr.ch/concepts/index.md#systemd-sysexts>). `systemd-sysupdate.timer` runs an update for it on every fire and touches the same `/run/reboot-required` when a new image is fetched. containerd is part of the OS image and updates with it.

### Nodes are pinned to a minor series

The sysupdate config points at `https://extensions.flatcar.org/extensions/`, not at the boot server, and its `MatchPattern` names the major.minor from `kubernetes_version` in `ansible/inventory.yaml` with only the patch left as the `@v` wildcard — see `ansible/templates/sysupdate_kubernetes.conf.j2`.

Patch releases inside the series are picked up automatically; the next minor is not, because an unattended minor jump leaves kubelets ahead of a control plane that refuses to talk to them. sysext-bakery publishes a floating `kubernetes.conf` (`kubernetes-@v-%a.raw`) as well, which would stage a minor kubeadm cannot skip to, on whichever node checks first. `ansible/templates/sysupdate_kubernetes.conf.j2` renders the pinned file from the inventory and the Ignition config inlines it, so the source of every later sysext is reviewed in Git rather than fetched; `make config` also writes it to `output/http/kubernetes.conf` for running nodes.

```bash
ssh core@<node> 'ls -l /etc/extensions/ /opt/extensions/kubernetes/'
ssh core@<node> cat /etc/sysupdate.kubernetes.d/kubernetes.conf   # confirm the MatchPattern is pinned
```

## Upgrading a minor version deliberately

**kubeadm allows one minor at a time.** v1.34 to v1.37 is three passes through this procedure, each with its release notes read. A newly provisioned node skips all of it: it installs `kubernetes_version` directly and gets the pinned sysupdate config from the boot server.

The order is upstream's: the first control plane, the other control planes, then the workers, because a kubelet may run older than the API server but never newer. The sysext swaps kubeadm and kubelet together at the reboot, so the sysupdate config goes to one group at a time: Kured (`concurrency` in `payload/platform/kured/application.yaml`) reboots one node at a time but in no particular order, and a config pushed to every node at once hands the workers a new kubelet before the API server has moved.

1. Update `kubernetes_version` in `ansible/inventory.yaml`.
2. Fetch the new sysext and regenerate the Ignition and sysupdate configs.

   ```bash
   make download && make config
   ```
3. Stage the new sysext on the first control plane only. The sysupdate config was written once at install time and nothing rewrites it. The boot server is not needed: the node reboots from its own disk — see [Every boot after the first](<https://homelab.wlkr.ch/architecture/boot-process/index.md#every-boot-after-the-first>).

   ```bash
   scp output/http/kubernetes.conf core@<node>:/tmp/kubernetes.conf
   ssh core@<node> sudo mv /tmp/kubernetes.conf /etc/sysupdate.kubernetes.d/kubernetes.conf
   ssh core@<node> sudo systemctl start systemd-sysupdate
   ```
4. Reboot it to pick up the sysext — see [Rebooting a node](<https://homelab.wlkr.ch/operations/nodes/index.md#rebooting-a-node>). Kured would do it once sysupdate sets the sentinel, but by hand the next step follows at once.
5. On that node, run the kubeadm upgrade; the sysext swapped the binaries and nothing else. Always pass `--skip-phases addon/kube-proxy`, because a cluster built before `proxy.disabled` was set in `kubeadm.yaml.j2` still has `proxy: {}` in its stored `kubeadm-config`, and without the flag the upgrade redeploys `kube-proxy` beside Cilium; the flag is harmless on a cluster built since. Always pass `--patches /etc/kubernetes/patches` too: the static pods' memory requests and CoreDNS's spread constraint come from that directory and an upgrade rewrites the manifests without them otherwise.

   ```bash
   sudo kubeadm upgrade apply <version> --skip-phases addon/kube-proxy --patches /etc/kubernetes/patches
   ```
6. The other control planes, one at a time: step 3, then step 4, then `kubeadm upgrade node` with the same `--patches` flag. The reboot comes first because the sysext swaps the binaries only then, and `upgrade node` has to run the new kubeadm. It takes the version from the `kubeadm-config` that `upgrade apply` stored and rewrites the static pod manifests on this node.

   ```bash
   sudo kubeadm upgrade node --patches /etc/kubernetes/patches
   ```
7. The workers, one at a time: step 3, then step 4, then `kubeadm upgrade    node`, which only fetches the new kubelet configuration.

   ```bash
   sudo kubeadm upgrade node
   ```

> [!NOTE]
>
> Renovate keeps `kubernetes_version`, `flatcar_version` and `syslinux_version` current in `ansible/inventory.yaml`; which bumps automerge is in [Automerge policy](<https://homelab.wlkr.ch/development/maintenance/index.md#automerge-policy>).

## Platform components

Everything in `payload/` is upgraded by ArgoCD when Renovate bumps a `targetRevision` and the PR merges. Cilium, the Gateway API CRDs and ArgoCD are installed by `make install-cilium` and `make install-argo` before ArgoCD exists, but from the same `targetRevision`, so a rebuild lands where the cluster already is — see [Version pins](<https://homelab.wlkr.ch/architecture/gitops/index.md#version-pins>).

## Control-plane certificates

kubeadm issues the control-plane certificates for one year and renews them only during `kubeadm upgrade`. Patch updates arrive as a sysext and never run kubeadm, and a minor is [a manual procedure](<#upgrading-a-minor-version-deliberately>), so a year without one takes the API servers, the etcd peers and the front proxy down together. The kubelet certificates are the exception: the serving certificate is rotated through [kubelet-csr-approver](<https://homelab.wlkr.ch/platform/metrics-server/index.md>) and the client certificate by the kubelet itself.

`payload/platform/monitoring/control-plane-certificate-rules.yaml` warns thirty days ahead on the client certificates the API server sees, which kubeadm issues together with the serving ones; the chart's own `KubeClientCertificateExpiration` takes over at seven days and one day. The renewal is done by hand, once a year or when the alert fires, on every control plane in turn:

1. Read the remaining lifetime; every line but the CA should have well over thirty days.

   ```bash
   ssh <control plane> sudo kubeadm certs check-expiration
   ```
2. Renew everything kubeadm issued on that node. The CA is not touched.

   ```bash
   ssh <control plane> sudo kubeadm certs renew all
   ```
3. Restart the static pods so they load the new certificates: move the manifests out, wait until `crictl` lists none of the four, move them back. One node at a time, since the API server and etcd on it are down in between.

   ```bash
   ssh <control plane> 'sudo mkdir -p /root/manifests-stopped && sudo mv /etc/kubernetes/manifests/*.yaml /root/manifests-stopped/ && until [ -z "$(sudo crictl ps -q --name "kube-apiserver|kube-controller-manager|kube-scheduler|etcd")" ]; do sleep 2; done && sudo mv /root/manifests-stopped/*.yaml /etc/kubernetes/manifests/'
   ```
4. After the last node, refresh the admin kubeconfig: `admin.conf` was renewed too, and the copy in `output/kubeconfig` carries the old client certificate.

   ```bash
   make kubeconfig
   ```
