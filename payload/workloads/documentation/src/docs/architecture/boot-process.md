# Boot & Bootstrap Process

This document details the flow of data from the initial PXE boot to a fully operational Kubernetes node.

## 1. Initial Provisioning (PXE + Ignition)

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
    
    Target->>Deploy: 7. TFTP Request (lpxelinux.0 / syslinux.efi)
    Deploy-->>Target: 8. TFTP File Transfer
    
    Target->>Deploy: 9. HTTP Request (Flatcar Kernel + Initrd)
    Deploy-->>Target: 10. HTTP File Transfer
    
    Target->>Deploy: 11. HTTP Request (Ignition Config)
    Note over Target,Deploy: Kernel boot parameter: ignition.config.url=http://...
    Deploy-->>Target: 12. JSON Config Response
    
    Target->>Target: 13. Flatcar Boot & Install (Ignition)
    
    Target->>Deploy: 14. Systemd-sysupdate Request (Sysexts)
    Deploy-->>Target: 15. HTTP File Transfer (k8s + containerd)
    
    Target->>Target: 16. Systemd Unit runs Kubeadm
    Note over Target: Node is Ready but CNI not installed
```

## 2. Post-Installation Bootstrap

Once the OS is installed and Kubeadm has initialized the node, we manually trigger the bootstrap of cluster components.

```mermaid
sequenceDiagram
    participant Admin as User
    participant Deploy as Deployment Host
    participant Target as Target Host (Node)

    Admin->>Deploy: 1. make untaint
    Deploy->>Target: 2. Kubectl Taint Node
    Admin->>Deploy: 3. make install-core
    Note over Deploy,Target: Installs Cilium (CNI) & Cert-Manager
    Admin->>Deploy: 4. make install-argo
    Deploy->>Target: 5. Helm Install ArgoCD
    Admin->>Deploy: 6. make bootstrap-apps
    Deploy->>Target: 7. Apply root.yaml (GitOps)
```
