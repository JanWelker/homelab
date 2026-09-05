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
because reboots are meant to be coordinated by something that drains the node
first. In its place, a `flatcar-reboot-sentinel.timer` polls
`update_engine_client -status` every ten minutes and touches
`/run/reboot-required` once an update is staged — the same marker the sysupdate
drop-ins below already use, and the file a reboot coordinator watches.

Writing the marker is all this does. Until something consumes it, an OS update
is still applied only when you reboot the node yourself, following
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

### Nodes are pinned to a minor series

The sysupdate configs served to the nodes point at
`https://extensions.flatcar.org/extensions/`, not at this project's boot server.
They used to carry sysext-bakery's *floating* `MatchPattern`
(`kubernetes-@v-%a.raw`), which meant a node tracked whatever upstream published
as newest and could stage a Kubernetes minor kubeadm refuses to skip to.

The generated configs now pin the major.minor from `ansible/inventory.yaml`:

```ini
[Source]
Type=url-file
Path=https://extensions.flatcar.org/extensions/kubernetes/
MatchPattern=kubernetes-v1.37.@v-%a.raw
```

Patch releases inside `v1.37` are still picked up automatically, which is what
you want — `v1.38` is not, which is the point. sysext-bakery publishes exactly
this file as `kubernetes-v1.37.conf` alongside the floating one, and the
generated config is byte-identical to it. containerd gets the same treatment
even though upstream ships no pinned variant for it.

The pinning happens in
`ansible/playbooks/tasks/download_sysext.yaml`, which asserts that the rewrite
landed. If sysext-bakery ever changes the format, `make download` fails rather
than quietly handing the nodes a floating config again.

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
   procedure — v1.34 to v1.35, then v1.36, then v1.37 — not one.
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

5. Reboot nodes one at a time, control plane first, per
   [Rebooting a node](index.md#rebooting-a-node).
6. On a control-plane node, run `kubeadm upgrade` as the Kubernetes release
   notes require. The sysext swaps the binaries; it does not run the upgrade
   steps kubeadm needs for control-plane components.

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
itself were installed by Helm before ArgoCD existed, with versions pinned in the
`Makefile`. Renovate does not track those pins.
