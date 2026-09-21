---
description: "How the OS, Kubernetes, and containerd get updated on these nodes, and why nothing takes effect until a reboot."
---

# Updates & Upgrades

Three mechanisms update a node, and all three land their changes on disk and
wait for a reboot. A node can be four patch releases behind while reporting
that everything is up to date.

## OS updates

Flatcar downloads OS updates in the background into the passive half of its
A/B partition pair. `locksmithd`, which would normally coordinate the reboot,
is masked in `ansible/templates/butane_node_config.yaml.j2`, because a reboot
must drain the node first. In its place `flatcar-reboot-sentinel.timer` polls
`update_engine_client -status` and touches `/run/reboot-required` once an
update is staged. [Kured](../platform/kured.md) consumes that marker: it
drains, reboots and uncordons, one node at a time and only while Ceph and etcd
are healthy. To move sooner, see [Rebooting a node](nodes.md#rebooting-a-node).

Three alerts in `monitoring/node-update-rules.yaml` watch that this actually
happens, because Trivy scans containers and not the host: `NodeRebootPending`
when a node has carried the marker for two days, `NodeOsVersionDrift` and
`NodeKubeletVersionDrift` when the nodes have run different Flatcar or kubelet
versions for three.

```bash
ssh core@<node> 'cat /etc/os-release; systemctl status update-engine --no-pager'
```

## Kubernetes and containerd

Both are [systemd sysexts](../concepts.md#systemd-sysexts).
`systemd-sysupdate.timer` runs an update for both on every fire and touches
the same `/run/reboot-required` when a new image is fetched.

### Nodes are pinned to a minor series

The sysupdate configs point at `https://extensions.flatcar.org/extensions/`,
not at the boot server, and pin the major.minor from `ansible/inventory.yaml`:

```ini
[Source]
Type=url-file
Path=https://extensions.flatcar.org/extensions/kubernetes/
MatchPattern=kubernetes-v1.37.@v-%a.raw
```

Patch releases inside the series are picked up automatically; the next minor is
not, because an unattended minor jump leaves kubelets ahead of a control plane
that refuses to talk to them. sysext-bakery publishes exactly this file as
`kubernetes-v1.37.conf`; the floating alternative (`kubernetes-@v-%a.raw`)
would stage a minor kubeadm cannot skip to, on whichever node checks first.
containerd gets the same pin even though upstream ships none.
`ansible/playbooks/tasks/download_sysext.yaml` asserts the rewrite landed, so a
format change upstream fails `make download` rather than handing the nodes a
floating config.

```bash
ssh core@<node> 'ls -l /etc/extensions/ /opt/extensions/kubernetes/'
cat /etc/sysupdate.kubernetes.d/kubernetes.conf   # confirm the MatchPattern is pinned
```

## Upgrading a minor version deliberately

**kubeadm allows one minor at a time.** v1.34 to v1.37 is three passes through
this procedure, each with its release notes read. A newly provisioned node
skips all of it: it installs `kubernetes_version` directly and gets the pinned
sysupdate config from the boot server.

1. Update `kubernetes_version` (and `containerd_version` if relevant) in
   `ansible/inventory.yaml`.
2. Fetch the new sysext and regenerate the Ignition and sysupdate configs.

    ```bash
    make download && make config
    ```

3. Replace the sysupdate config on every running node; it was written once at
   install time and nothing rewrites it. The boot server is not needed: the
   node reboots from its own disk — see
   [Every boot after the first](../architecture/boot-process.md#every-boot-after-the-first).

    ```bash
    scp output/http/kubernetes.conf core@<node>:/tmp/kubernetes.conf
    ssh core@<node> sudo mv /tmp/kubernetes.conf /etc/sysupdate.kubernetes.d/kubernetes.conf
    ssh core@<node> sudo systemctl start systemd-sysupdate
    ```

4. Reboot nodes to pick up the sysext: Kured does it once sysupdate sets the
   sentinel, or follow [Rebooting a node](nodes.md#rebooting-a-node).
5. On a control-plane node, run the kubeadm upgrade; the sysext swaps the
   binaries and nothing else. Always pass `--skip-phases addon/kube-proxy`,
   because a cluster built before `proxy.disabled` was set in `kubeadm.yaml.j2`
   still has `proxy: {}` in its stored `kubeadm-config`, and without the flag
   the upgrade redeploys `kube-proxy` beside Cilium; the flag is harmless on a
   cluster built since.

    ```bash
    sudo kubeadm upgrade apply <version> --skip-phases addon/kube-proxy
    ```

!!! note
    Renovate keeps `kubernetes_version`, `containerd_version`, `flatcar_version` and `syslinux_version` current in `ansible/inventory.yaml`, and patch and minor bumps automerge. Merging one changes what a **newly provisioned** node installs, not a running node — see [Maintenance](../development/maintenance.md#automerge-policy).

## Platform components

Everything in `payload/` is upgraded by ArgoCD when Renovate bumps a
`targetRevision` and the PR merges. Cilium, the Gateway API CRDs and ArgoCD are
installed by `make install-cilium` and `make install-argo` before ArgoCD exists,
but from the same `targetRevision`, so a rebuild lands where the cluster
already is — see [Version pins](../architecture/gitops.md#version-pins).
