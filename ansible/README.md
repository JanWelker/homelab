# Ansible Configuration

This directory contains the Ansible playbooks and configuration used to provision
the Flatcar cluster.

## Directory Structure

```text
ansible/
├── inventory.yml       # Host definitions (MAC addresses, IPs, Roles)
├── playbooks/
│   ├── config.yml      # Generates Ignition and Kubeadm configurations
│   ├── download.yml    # Downloads required artifacts (OS images, binaries)
│   ├── kubeconfig.yml  # Retrieves kubeconfig from the control plane
│   └── tasks/          # Reusable tasks for downloads
└── templates/
    ├── butane_config.yaml.j2 # Template for Butane config (transpiled to Ignition)
    ├── kubeadm.yaml.j2       # Template for Kubeadm configuration
    └── pxe_config.j2         # Template for PXE boot menu
```

## Inventory

The `inventory.yml` file defines the cluster layout.

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

### `config.yml`

Generates all necessary configuration files for booting and bootstrapping the
nodes.

- Generates credentials (tokens, certificate keys).
- Creates Ignition configs (via Butane) for each host.
- Creates PXE boot menus for each host based on MAC address.
- Outputs to `output/http` and `output/tftp`.

### `download.yml`

Downloads external artifacts required for provisioning.

- Flatcar Kernel and Initrd.
- Systemd Sysext images (Kubernetes, Containerd).
- Syslinux bootloader files.

### `kubeconfig.yml`

Retrieves the admin `kubeconfig` file from the first available control plane
node after the cluster is bootstrapped.
