---
description: "How a bare metal node goes from power-on to joined cluster member via PXE, Ignition, and Kubeadm."
---

# Boot & Bootstrap Process

This document details the flow of data from the initial PXE boot to a fully
operational Kubernetes node.

## 1. Preparation (on the deployment host)

Before any node is powered on, the operator runs `make config` to generate the
per-host Ignition and PXE configs, then `make serve` to start the TFTP and HTTP
servers. See the [Quickstart](../quickstart.md) for the exact sequence.

## 2. Network Boot

```mermaid
sequenceDiagram
    participant Node
    participant DHCP
    participant Server as Boot Server

    Node->>DHCP: 1. PXE request
    DHCP-->>Node: 2. IP, next-server, filename
    Node->>Server: 3. TFTP bootloader
    Server-->>Node: 4. syslinux + menu
    Node->>Server: 5. HTTP kernel, initrd
    Server-->>Node: 6. Flatcar kernel, initrd
```

The DHCP server is external to this project. It must hand out the boot server's
IP as `next-server` and a syslinux filename — see the
[Quickstart prerequisites](../quickstart.md#prerequisites).

## 3. Install & Bootstrap

```mermaid
sequenceDiagram
    participant Node
    participant Server as Boot Server

    Node->>Server: 7. HTTP Ignition config
    Note over Node,Server: URL comes from the ignition.config.url kernel parameter
    Server-->>Node: 8. Ignition JSON
    Node->>Node: 9. Partition disk, install Flatcar, reboot
    Node->>Server: 10. HTTP sysext images
    Server-->>Node: 11. kubernetes, containerd
    Node->>Node: 12. systemd unit runs kubeadm
    Note over Node: Node is NotReady - no CNI yet
```

## 4. Post-Installation Bootstrap

Once Kubeadm has initialized the control plane, the remaining components are
installed from the deployment host.

```mermaid
sequenceDiagram
    participant Admin as Operator
    participant Deploy as Deployment Host
    participant Cluster

    Admin->>Deploy: 1. make install-core
    Deploy->>Cluster: 2. Helm install Cilium, cert-manager
    Note over Cluster: Nodes become Ready
    Admin->>Deploy: 3. make install-argo
    Deploy->>Cluster: 4. Helm install ArgoCD
    Admin->>Deploy: 5. make bootstrap-apps
    Deploy->>Cluster: 6. Apply root.yaml (App-of-Apps)
```

!!! note
    `make untaint` is **not** part of this flow. It removes the control-plane
    `NoSchedule` taint and applies only to a single-node cluster. The layout in
    [Architecture Overview](index.md#cluster-layout) has dedicated workers, so
    the taint should stay in place.
