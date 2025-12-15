# Architecture

This project provides a mechanism to deploy a Bare Metal Kubernetes Cluster using Flatcar Container Linux and Kubeadm.

## Components

### 1. Deployment Host (Local Machine)

The machine where this project is executed.

* **Ansible**: Responsible for generating the configuration files (Ignition, Kubeadm config) based on templates and variables.
* **Python Boot Server**: A custom Python script that runs:
  * **TFTP Server**: Serves the Bootloader (syslinux.efi/pxelinux.0) and config.
  * **HTTP Server**: Serves Ignition configs, Flatcar Kernel/Initrd, and Sysext images (`.raw`) + configs (`.conf`).
* **Artifacts**: Directory containing downloaded OS images (Flatcar) and generated configs.

### 2. Target Host (Bare Metal Node)

The physical machine to be provisioned.

* **PXE Client**: NIC boots via network (DHCP provided externally).
* **Flatcar OS**: The operating system loaded into RAM and then installed to disk.
* **Kubeadm**: The tool used to bootstrap the Kubernetes cluster.

## Interfaces & Data Flow

```mermaid
sequenceDiagram
    participant Admin as User
    participant Deploy as Deployment Host
    participant DHCP as External DHCP
    participant Target as Target Host (Node)

    Admin->>Deploy: 1. Configure Inventory & Run Ansible
    Deploy->>Deploy: 2. Generate Ignition & Kubeadm Configs
    Admin->>Deploy: 3. Start Python Boot Server (TFTP/HTTP)
    Admin->>Target: 4. Boots Machines
    
    Target->>DHCP: 5. DHCP Request (PXE Boot)
    DHCP-->>Target: 6. DHCP Ack (IP + Next-Server IP + Filename)
    
    Target->>Deploy: 7. TFTP Request (pxelinux.0 / ipxe.efi)
    Deploy-->>Target: 8. TFTP File Transfer
    
    Target->>Deploy: 9. HTTP Request (Flatcar Kernel + Initrd)
    Deploy-->>Target: 10. HTTP File Transfer
    
    Target->>Deploy: 11. HTTP Request (Ignition Config)
    Note over Target,Deploy: Kernel boot parameter: ignition.config.url=http://...
    Deploy-->>Target: 12. JSON Config Response
    
    Target->>Target: 13. Flatcar Boot & Install (Ignition)
    
    Target->>Deploy: 14. Systemd-sysupdate Request (Sysexts)
    Deploy-->>Target: 15. HTTP File Transfer (k8s + cilium + containerd)
    
    Target->>Target: 16. Systemd Unit runs Kubeadm
    Target->>Target: 17. Systemd Unit installs Cilium (WireGuard)

```

### Post-Installation Flow

```mermaid
sequenceDiagram
    participant Admin as User
    participant Deploy as Deployment Host
    participant Target as Target Host (Node)

    Admin->>Deploy: 1. make untaint
    Deploy->>Target: 2. Kubectl Taint Node
    Admin->>Deploy: 3. make install-argo
    Deploy->>Target: 4. Helm Install ArgoCD
```

## Directory Structure

```text
.
├── ansible
│   ├── inventory.yml       # Host definitions (MAC addresses, IPs, Roles)
│   ├── playbooks
│   │   ├── download.yml    # Orchestrate downloads
│   │   ├── tasks           # Download Task definitions
│   │   │   ├── download_flatcar.yml
│   │   │   ├── download_sysext.yml
│   │   │   └── download_syslinux.yml
│   │   └── config.yml      # Generate configs
│   └── templates
│       ├── butane_config.yaml.j2 # Butane config template (transpiles to Ignition)
│       └── kubeadm.yaml.j2
├── boot_server
│   └── serve.py            # Python script for HTTP & TFTP
├── output                  # Generated files & Artifacts
│   ├── credentials         # Security artifacts (Kubeadm tokens, Certificate Keys)
│   ├── http                # Ignition configs, Flatcar artifacts, Sysext images (served via HTTP)
│   ├── kubeconfig          # Admin Kubeconfig file (generated after cluster is ready)
│   ├── tftp                # PXE bootloader & configs (served via TFTP)
│   └── tmp                 # Temporary download/extraction workspace
│       ├── butane          # Generated Butane YAMLs
│       ├── kubeadm         # Generated Kubeadm server configurations (for debugging)
│       └── syslinux        # Extracted Syslinux files
├── payload                 # K8s Manifests & Bootstrap scripts
│   ├── apps                # ArgoCD Applications
│   ├── bootstrap           # Initial cluster bootstrap resources (ArgoCD)
│   └── core                # Core infrastructure manifests
└── README.md
```

## Technologies

* **OS**: Flatcar Container Linux
* **Orchestrator**: Kubernetes (via Kubeadm)
* **Config Gen**: Ansible (Jinja2 templates)
* **Serving**: Python (Standard Library + `tftpy`)
