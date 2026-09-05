---
description: "How the OS, Kubernetes, and containerd actually get updated on these nodes, and why a reboot is always a manual step."
---

# Updates & Upgrades

Three separate mechanisms update a node, and none of them completes on its own.
All three land their changes on disk and then wait for a reboot that nothing
schedules.

## OS updates

Flatcar downloads OS updates in the background and writes them to the passive
half of its A/B partition pair, so the new image is ready but inactive.
Normally `locksmithd` would then coordinate a reboot across the cluster.

**This project masks `locksmithd`** (`ansible/templates/butane_config.yaml.j2`),
so nothing ever triggers that reboot. An OS update is applied only when you
reboot the node yourself, following
[Rebooting a node](index.md#rebooting-a-node).

Check what a node is running and whether an update is staged:

```bash
ssh core@<node> 'cat /etc/os-release; systemctl status update-engine --no-pager'
```

## Kubernetes and containerd

These are delivered as [systemd sysexts](../architecture/index.md#systemd-sysexts).
`systemd-sysupdate.timer` is enabled, and the drop-ins run an update for both
extensions on every fire. When a new image is fetched, the unit touches
`/run/reboot-required` — a marker file that, with `locksmithd` masked, nothing
acts on. The new extension takes effect at the next boot.

!!! warning
    The sysupdate configs served to the nodes point at
    `https://extensions.flatcar.org/extensions/`, not at this project's boot
    server, and match on `kubernetes-@v-%a.raw`. Nodes therefore track whatever
    version upstream publishes as newest — `kubernetes_version` in
    `ansible/inventory.yaml` pins only the image baked in at first boot. Left
    alone long enough, a node can stage a Kubernetes minor version that kubeadm
    does not support skipping to, and apply it on the next reboot.

Inspect what is currently staged on a node:

```bash
ssh core@<node> 'ls -l /etc/extensions/ /opt/extensions/kubernetes/'
```

To pin a version instead, edit the `[Source]` `Path`/`MatchPattern` in the
sysupdate config that the boot server serves, or disable
`systemd-sysupdate.timer` on the nodes and drive upgrades from `inventory.yaml`
by hand.

## Upgrading deliberately

Because sysupdate tracks upstream, a controlled Kubernetes upgrade means
choosing the version and applying it one node at a time rather than letting the
timer decide:

1. Confirm the jump is supported. kubeadm allows one minor version at a time,
   control plane first.
2. Update `kubernetes_version` (and `containerd_version` if relevant) in
   `ansible/inventory.yaml`.
3. `make download && make config` to fetch the new sysext and regenerate the
   Ignition configs. This matters for any node you later reprovision — an
   existing node picks the image up from `extensions.flatcar.org`.
4. Reboot nodes one at a time, control plane first, per
   [Rebooting a node](index.md#rebooting-a-node).
5. On a control-plane node, run `kubeadm upgrade` as the Kubernetes release
   notes require. The sysext swaps the binaries; it does not run the upgrade
   steps kubeadm needs for control-plane components.

!!! note
    Renovate keeps `kubernetes_version`, `containerd_version`, `flatcar_version`
    and `syslinux_version` current in `ansible/inventory.yaml` under the
    `Core Infrastructure` group, which always requires review. Merging one of
    those PRs changes what a **newly provisioned** node installs; it does not
    change a running node.

## Platform components

Everything in `payload/` is upgraded by ArgoCD when Renovate bumps a
`targetRevision` and the PR merges. No manual step is involved. See
[Maintenance](../development/maintenance.md).

The exception is anything installed by `make install-core` and
`make install-argo` — Cilium, cert-manager, the Gateway API CRDs and ArgoCD
itself were installed by Helm before ArgoCD existed, with versions pinned in the
`Makefile`. Renovate does not track those pins.
