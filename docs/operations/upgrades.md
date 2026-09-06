---
description: "How the OS, Kubernetes, and containerd actually get updated on these nodes, and why nothing takes effect until a reboot."
---

# Updates & Upgrades

Three separate mechanisms update a node, and none of them completes on its own.
All three land their changes on disk and then wait for a reboot.

This is the part that surprises people coming from a normal distro. Nothing here
is "installed" in the sense you are used to. The new version is sitting on the
disk, fully downloaded, politely doing nothing, until the machine is restarted.
A node can be four patch releases behind while reporting that everything is up
to date, because from its point of view everything *is* up to date — just not
running yet.

## OS updates

Flatcar downloads OS updates in the background and writes them to the passive
half of its A/B partition pair, so the new image is ready but inactive.
Normally `locksmithd` would then coordinate a reboot across the cluster.

**This project masks `locksmithd`** (`ansible/templates/butane_config.yaml.j2`),
because reboots are meant to be coordinated by something that drains the node
first. In its place, a `flatcar-reboot-sentinel.timer` polls
`update_engine_client -status` every ten minutes and touches
`/run/reboot-required` once an update is staged — the same marker the sysupdate
drop-ins below already use, and the file a reboot coordinator watches.

[Kured](../platform/kured.md) consumes that marker: it drains the node, reboots
it, and uncordons it, one node at a time inside a nightly window and only while
Ceph and etcd are healthy. Rebooting by hand is still available, and is what to
do when you do not want to wait for the window — see
[Rebooting a node](index.md#rebooting-a-node).

`locksmithd` is masked rather than merely disabled, incidentally, because a
service that reboots a storage node without draining it first is not a feature,
it is a scheduled outage with good manners.

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

### Nodes are pinned to a minor series

The sysupdate configs served to the nodes point at
`https://extensions.flatcar.org/extensions/`, not at this project's boot server.
The generated configs pin the major.minor from `ansible/inventory.yaml`:

```ini
[Source]
Type=url-file
Path=https://extensions.flatcar.org/extensions/kubernetes/
MatchPattern=kubernetes-v1.37.@v-%a.raw
```

Patch releases inside `v1.37` are still picked up automatically, which is what
you want — `v1.38` is not, which is very much the point. An unattended jump to a
new minor is how a cluster wakes up with kubelets a version ahead of a control
plane that refuses to talk to them. sysext-bakery publishes exactly
this file as `kubernetes-v1.37.conf`, and the generated config is byte-identical
to it. containerd gets the same treatment even though upstream ships no pinned
variant for it.

The alternative sysext-bakery offers is a *floating* `MatchPattern`
(`kubernetes-@v-%a.raw`). A node on that one tracks whatever upstream publishes
as newest, which means it can stage a Kubernetes minor kubeadm refuses to skip
to — and it will do so overnight, without asking, on whichever node happens to
check first.

The pinning happens in
`ansible/playbooks/tasks/download_sysext.yaml`, which asserts that the rewrite
landed. If sysext-bakery ever changes the format, `make download` fails rather
than quietly handing the nodes a floating config.

Inspect what is currently staged on a node:

```bash
ssh core@<node> 'ls -l /etc/extensions/ /opt/extensions/kubernetes/'
cat /etc/sysupdate.d/kubernetes.conf   # confirm the MatchPattern is pinned
```

## Upgrading a minor version deliberately

Changing `kubernetes_version` to a new minor changes what the nodes track, so
this is the deliberate path:

1. Confirm the jump is supported. **kubeadm allows one minor version at a
   time.** Going from v1.34 to v1.37 is three separate passes through this
   procedure — v1.34 to v1.35, then v1.36, then v1.37 — not one. There is no
   shortcut, there has never been a shortcut, and the release notes for each
   intermediate version are not optional reading.
2. Update `kubernetes_version` (and `containerd_version` if relevant) in
   `ansible/inventory.yaml`.
3. `make download && make config`. This fetches the new sysext, regenerates the
   Ignition configs, and regenerates the sysupdate configs with the new pinned
   `MatchPattern`.
4. Serve the new sysupdate config to the running nodes. They read it from
   `/etc/sysupdate.d/kubernetes.conf`, which was written at provisioning time,
   so a node that is not being reprovisioned needs the file replaced:

    ```bash
    scp output/http/kubernetes.conf core@<node>:/tmp/kubernetes.conf
    ssh core@<node> sudo mv /tmp/kubernetes.conf /etc/sysupdate.d/kubernetes.conf
    ssh core@<node> sudo systemctl start systemd-sysupdate
    ```

5. Reboot nodes to pick up the new sysext. Kured does this on its own once
   sysupdate sets the sentinel, one node at a time; to move faster, follow
   [Rebooting a node](index.md#rebooting-a-node) instead.
6. On a control-plane node, run `kubeadm upgrade` as the Kubernetes release
   notes require. The sysext swaps the binaries; it does not run the upgrade
   steps kubeadm needs for control-plane components. Swapping binaries and
   calling it an upgrade is exactly the kind of thing that works on five nodes
   and then does not work on the sixth.

!!! note
    A **newly provisioned** node skips all of this — it installs
    `kubernetes_version` directly and gets the pinned sysupdate config from the
    boot server. Only long-running nodes need step 4.

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
itself. They are installed by Helm during bootstrap, before ArgoCD exists to
manage them, with versions pinned in the `Makefile`. Renovate does not track
those pins, so they drift quietly and only reveal themselves the next time
somebody rebuilds a cluster from scratch and gets a different result. Worth a
glance whenever you touch the `Makefile` anyway.
