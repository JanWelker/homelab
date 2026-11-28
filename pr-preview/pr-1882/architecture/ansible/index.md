# Ansible Configuration

Ansible never edits a node's files: it renders Ignition configs on the deployment host — the one the PXE environment [runs to install](<https://homelab.wlkr.ch/architecture/boot-process/index.md#3-install-bootstrap>), the node config, and a reprovision variant — and Ignition applies them on a first boot. A change under `ansible/` reaches a running node by a [reprovision](<https://homelab.wlkr.ch/operations/nodes/index.md#reprovisioning-a-node>). The directory layout is in [Repository layout](<https://homelab.wlkr.ch/architecture/index.md#repository-layout>).

## Inventory

`ansible/inventory.yaml` is the single source of truth for the machines; every generated artifact comes from it, and the values are baked into the output rather than read at boot. The variables are described in [Quickstart step 2](<https://homelab.wlkr.ch/quickstart/index.md#setup>). Every playbook targets the `k8s_nodes` group, the parent of `control_plane` and `workers`, which carries `ansible_user` and the SSH options.

> [!WARNING]
>
> **Every edit needs a regeneration, and some need two**
>
> `make config` re-renders the Ignition configs, PXE menus and router config from the inventory; `make download` fetches what `flatcar_channel`, `flatcar_version`, `syslinux_version` and `kubernetes_version` name. The Flatcar, Kubernetes and kube-vip versions are rendered into the configs too, so a version bump needs `make artifacts`, which runs both. Which target reads which variable is in the [Quickstart table](<https://homelab.wlkr.ch/quickstart/index.md#setup>). An edit followed by neither changes nothing at all, silently.

`bootstrap_cluster` in the `control_plane` group decides whether the first control-plane host renders `kubeadm init` (`true`, the first install) or, like every other control plane, `kubeadm join --control-plane` (`false`, every rebuild). A rebuilt node that ran `init` would start a second cluster on the same VIP — see [Rebuilding a node](<https://homelab.wlkr.ch/operations/nodes/index.md#rebuilding-or-repartitioning-a-node>).

`kube_vip_version` only sets the bootstrap static pod that gives `kubeadm init` a VIP; once ArgoCD syncs, `payload/platform/kube-vip/` takes over and its image is what runs — see [Control Plane VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>).

## Playbooks

| Playbook | Does |
| --- | --- |
| `config.yaml` | Generates the bootstrap token, certificate key, etcd encryption keys and each node's SSH host key into `output/credentials/` (mode `0700`), and `output/known_hosts` from the host keys; renders the Ignition configs via Butane into `output/http` (mode `0600`, the node config first: the installer config pins its sha512) and the reprovision configs with each node's checksum into `output/reprovision`, the per-MAC PXE menus into `output/tftp/pxelinux.cfg/`, the router's FRR config into `output/router/frr-bgp.conf`, and a kubeadm config copy into `output/tmp/` |
| `download.yaml` | Fetches the Flatcar kernel, initrd and OS image, checked against the `.DIGESTS.asc` that `gpgv` verifies; the Kubernetes sysext; and the syslinux files, checked against `sha256sums.asc`. The signing keys are trusted through fingerprints pinned in the playbook |
| `reinstall.yaml` | Rewrites `DEFAULT` in the generated PXE menus — `install` to arm, `localboot` to disarm. `make reinstall` and `make reinstall-cancel` are the two directions, both taking `LIMIT=<host>`. Arming refuses while `bootstrap_cluster` is `true` and the API VIP answers, or when an [endpoint name](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md#endpoint-name>) is not the one the cluster publishes. `make config` keeps each menu's `DEFAULT`, so an armed node stays armed. See [Switching back to local boot](<https://homelab.wlkr.ch/architecture/boot-process/index.md#switching-back-to-local-boot>) |
| `reprovision.yaml` | `make reprovision LIMIT=<host>`: stages [`flatcar-reset`](<https://homelab.wlkr.ch/operations/nodes/index.md#reprovisioning-a-node>) with its reprovision config and sets Kured's sentinel |
| `kube-upgrade.yaml` | `make kube-upgrade`: [`kubeadm upgrade`](<https://homelab.wlkr.ch/operations/upgrades/index.md#kubernetes>) after a new sysext |
| `kube-certs.yaml` | `make kube-certs`: [certificate renewal](<https://homelab.wlkr.ch/operations/upgrades/index.md#control-plane-certificates>) |
| `kubeconfig.yaml` | Copies the admin kubeconfig over SSH to `output/kubeconfig`, mode `0600`, with the VIP as its `server` |

`config.yaml` is safe to re-run: the `password` lookup reads an existing credential file back, so the encryption key every control-plane node shares stays the one etcd's Secrets were encrypted with.

`config.yaml` renews the join credentials on the first control-plane host that answers with a running cluster, so the node being [replaced](<https://homelab.wlkr.ch/operations/nodes/index.md#replacing-a-failed-node>) may be any of them; it fails when none answers while `output/kubeconfig` exists, since a node booted then could not join, and skips the renewal only before the first bootstrap.

`kubeconfig.yaml` targets `control_plane[0]` with no fallback; if it is down, copy `/etc/kubernetes/admin.conf` off another control plane by hand. Keep a copy off the cluster: it is the credential you want when the web UIs are unreachable.
