---
description: "Ansible playbooks that generate node configuration, download artifacts, and retrieve the cluster kubeconfig."
---

# Ansible Configuration

The Ansible playbooks and configuration used to provision the Flatcar cluster.

Worth being clear about what Ansible is doing here, because it is not the usual
job. It never configures a node. It has no `hosts: all` play that SSHes in and
converges anything — it generates files on the deployment host, and Ignition
applies them once, on the first boot after a node is installed. Ansible is a
template engine with an inventory, and on an immutable OS that is exactly the
right amount of Ansible.

It generates two Ignition configs per host, from two templates: one the PXE
environment [runs to install](../architecture/boot-process.md#3-install-bootstrap),
and one `flatcar-install` embeds into the system it writes. Changing anything
under `ansible/` therefore takes a rebuild of the affected node, not a reboot.

## Directory Structure

```text
ansible/
├── inventory.yaml       # Host definitions (MAC addresses, IPs, Roles)
├── playbooks/
│   ├── config.yaml      # Generates Ignition and Kubeadm configurations
│   ├── download.yaml    # Downloads required artifacts (OS images, binaries)
│   ├── kubeconfig.yaml  # Retrieves kubeconfig from the control plane
│   ├── reinstall.yaml   # Flips DEFAULT in the generated PXE menus
│   └── tasks/          # Reusable tasks for downloads
└── templates/
    ├── butane_installer_config.yaml.j2 # PXE environment: wipe, install, reboot
    ├── butane_node_config.yaml.j2      # Installed system: partitions, files, units
    ├── kubeadm.yaml.j2                 # Template for Kubeadm configuration
    └── pxe_config.j2                   # Template for PXE boot menu
```

## Inventory

The `inventory.yaml` file defines the cluster layout, and it is the single
source of truth for everything about these machines. Every generated artifact
comes from it, and the values are baked into the output rather than read at
boot.

!!! warning "Every edit needs a regeneration, and some need two"
    `make config` re-renders the Ignition configs and PXE menus from the inventory. The four version variables are consumed by `download.yaml` instead, so changing one needs `make download` as well — `make artifacts` runs both. An edit followed by neither changes nothing at all, silently.

**Global variables** sit under `all.vars`: the four artifact versions
(`flatcar_version`, `kubernetes_version`, `containerd_version`,
`syslinux_version`) plus `flatcar_channel`, `kube_vip_version`,
`boot_server_ip`, `control_plane_vip`, `control_plane_vip_interface`,
`install_disk`, `pod_subnet` and `service_subnet`.

**Groups** nest one level deeper than they first appear:

| Group | Holds |
| --- | --- |
| `k8s_nodes` | The parent of both role groups, and what every playbook actually targets. Carries `ansible_user`, the Python interpreter path and the SSH options |
| `control_plane` | `role: control-plane` |
| `workers` | `role: worker` |

**Host variables**:

| Variable | Purpose |
| --- | --- |
| `ansible_host` | The static IP the node is given |
| `mac_address` | Selects which generated PXE menu the node picks up |
| `install_disk` | Overrides the global default for one host, as `freya` does |

## Playbooks

### `config.yaml`

Generates all necessary configuration files for booting and bootstrapping the
nodes.

- Generates credentials (bootstrap token, certificate key, etcd encryption key)
    into `output/credentials/`, mode `0700`.
- Creates Ignition configs (via Butane) for each host, into `output/http`.
- Creates PXE boot menus for each host based on MAC address, into
    `output/tftp/pxelinux.cfg/`.
- Renders the kubeadm config into `output/tmp/` as well, purely so it can be
    read. The copy that runs is the one inlined into the Ignition config.

### `download.yaml`

Downloads external artifacts required for provisioning.

- Flatcar kernel and initrd.
- The Flatcar OS image and its detached signature — by some distance the largest
    artifact, and the one the boot server narrates as "this is the long one".
- Systemd Sysext images (Kubernetes, Containerd), plus their sysupdate configs,
    rewritten to pin the major.minor from `inventory.yaml`.
- Syslinux bootloader files.

### `reinstall.yaml`

Rewrites `DEFAULT` in the generated PXE menus — `install` to arm a node,
`localboot` to disarm it. `make reinstall` and `make reinstall-cancel` are the
two directions, and both take `LIMIT=<host>` to act on one node. It touches only
`output/tftp/pxelinux.cfg/`, so the next `make config` regenerates the safe
default from the template regardless. See
[Boot Server &rarr; Switching back to local boot](boot-server.md#switching-back-to-local-boot).

### `kubeconfig.yaml`

Retrieves the admin `kubeconfig` over SSH once the cluster is bootstrapped and
writes it to `output/kubeconfig`, mode `0600`.

It targets `control_plane[0]` specifically — the first control-plane host in the
inventory, with no fallback. If that node is down the playbook fails even though
the other API servers are answering; copy `/etc/kubernetes/admin.conf` off any
of them by hand in that case.

Keep a copy of the result somewhere off the cluster. It is the credential you
will want on the day the web UIs are unreachable, and it lives in `output/`,
which is gitignored and therefore exactly as durable as the laptop it is on.
