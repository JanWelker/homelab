---
description: "Ansible playbooks that generate node configuration, download artifacts, and retrieve the cluster kubeconfig."
---

# Ansible Configuration

The Ansible playbooks and configuration used to provision the Flatcar cluster.

Worth being clear about what Ansible is doing here, because it is not the usual
job. It never configures a node. It has no `hosts: all` play that SSHes in and
converges anything — it generates files on the deployment host, and Ignition
applies them once at first boot. Ansible is a template engine with an inventory,
and on an immutable OS that is exactly the right amount of Ansible.

## Directory Structure

```text
ansible/
├── inventory.yaml       # Host definitions (MAC addresses, IPs, Roles)
├── playbooks/
│   ├── config.yaml      # Generates Ignition and Kubeadm configurations
│   ├── download.yaml    # Downloads required artifacts (OS images, binaries)
│   ├── kubeconfig.yaml  # Retrieves kubeconfig from the control plane
│   └── tasks/          # Reusable tasks for downloads
└── templates/
    ├── butane_config.yaml.j2 # Template for Butane config (transpiled to Ignition)
    ├── kubeadm.yaml.j2       # Template for Kubeadm configuration
    └── pxe_config.j2         # Template for PXE boot menu
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
