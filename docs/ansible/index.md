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
comes from it, which is why any edit has to be followed by `make config` — the
values are baked into the output, not read at boot.

- **Global Variables**: Flattened variables like versions (`kubernetes_version`,
  `flatcar_version`) and network settings.
- **Groups**:
  - `control_plane`: Master nodes.
  - `workers`: Worker nodes.
- **Host Variables**:
  - `mac_address`: Required for PXE boot configuration (configures specific
    menu for each MAC).
  - `ansible_host`: The static IP assigned to the node.

## Playbooks

### `config.yaml`

Generates all necessary configuration files for booting and bootstrapping the
nodes.

- Generates credentials (bootstrap token, certificate key, etcd encryption key).
- Creates Ignition configs (via Butane) for each host.
- Creates PXE boot menus for each host based on MAC address.
- Outputs to `output/http` and `output/tftp`.

### `download.yaml`

Downloads external artifacts required for provisioning.

- Flatcar Kernel and Initrd.
- Systemd Sysext images (Kubernetes, Containerd), plus their sysupdate configs,
    rewritten to pin the major.minor from `inventory.yaml`.
- Syslinux bootloader files.

### `kubeconfig.yaml`

Retrieves the admin `kubeconfig` file from the first available control plane
node after the cluster is bootstrapped.

Keep a copy of the result somewhere off the cluster. It is the credential you
will want on the day the web UIs are unreachable, and it lives in `output/`,
which is gitignored and therefore exactly as durable as the laptop it is on.
