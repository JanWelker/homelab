---
description: "Ansible playbooks that generate node configuration, download artifacts, and retrieve the cluster kubeconfig."
---

# Ansible Configuration

Ansible never configures a node here: it generates files on the deployment
host, and Ignition applies them once, on the first boot after a node is
installed.

It renders two Ignition configs per host from two templates — one the PXE
environment [runs to install](boot-process.md#3-install-bootstrap), one
`flatcar-install` embeds into the system it writes. Changing anything under
`ansible/` therefore takes a rebuild of the affected node, not a reboot. The
directory layout is in [Repository layout](index.md#repository-layout).

## Inventory

`ansible/inventory.yaml` is the single source of truth for the machines; every
generated artifact comes from it, and the values are baked into the output
rather than read at boot. The variables are described in
[Quickstart step 2](../quickstart.md#setup). Every playbook targets the
`k8s_nodes` group, the parent of `control_plane` and `workers`, which carries
`ansible_user`, the Python interpreter path and the SSH options.

!!! warning "Every edit needs a regeneration, and some need two"
    `make config` re-renders the Ignition configs and PXE menus from the inventory. The four version variables are consumed by `download.yaml` instead, so changing one needs `make download` as well — `make artifacts` runs both. An edit followed by neither changes nothing at all, silently.

`kube_vip_version` only sets the bootstrap static pod that gives `kubeadm init`
a VIP; once ArgoCD syncs, `payload/platform/kube-vip/` takes over and its image
is what runs — see [Control Plane VIP](../operations/control-plane-vip.md).

## Playbooks

| Playbook | Does |
| --- | --- |
| `config.yaml` | Generates the bootstrap token, certificate key and etcd encryption key into `output/credentials/` (mode `0700`); renders the Ignition configs via Butane into `output/http`, the per-MAC PXE menus into `output/tftp/pxelinux.cfg/`, and a readable copy of the kubeadm config into `output/tmp/` (the copy that runs is inlined into Ignition) |
| `download.yaml` | Fetches the Flatcar kernel, initrd, OS image and its detached signature; the Kubernetes and containerd sysext images with their sysupdate configs, rewritten to pin the major.minor from the inventory; and the syslinux bootloader files |
| `reinstall.yaml` | Rewrites `DEFAULT` in the generated PXE menus — `install` to arm, `localboot` to disarm. `make reinstall` and `make reinstall-cancel` are the two directions, both taking `LIMIT=<host>`. It touches only `output/tftp/pxelinux.cfg/`, so the next `make config` regenerates the safe default. See [Switching back to local boot](boot-process.md#switching-back-to-local-boot) |
| `kubeconfig.yaml` | Copies the admin kubeconfig over SSH to `output/kubeconfig`, mode `0600` |

`config.yaml` is safe to re-run: the `password` lookup reads an existing
credential file back instead of generating a new value, so the encryption key
every control-plane node shares stays the one etcd's Secrets were encrypted
with.

`kubeconfig.yaml` targets `control_plane[0]` — the first control-plane host in
the inventory, with no fallback. If that node is down, copy
`/etc/kubernetes/admin.conf` off any other control-plane node by hand. Keep a
copy of the result off the cluster: it is the credential you want when the web
UIs are unreachable, and `output/` is exactly as durable as the laptop it is on.
