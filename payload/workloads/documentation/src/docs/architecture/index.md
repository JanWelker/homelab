# Architecture Overview

This project provides a mechanism to deploy a Bare Metal Kubernetes Cluster using Flatcar Container Linux and Kubeadm.

## Components

### 1. Deployment Host (Local Machine)

The machine where this project is executed.

* **Ansible**: Responsible for generating the configuration files (Ignition, Kubeadm config) based on templates and variables.
* **Python Boot Server**: A custom Python script that runs:
  * **TFTP Server**: Serves the Bootloader (syslinux.efi/lpxelinux.0) and config.
  * **HTTP Server**: Serves Ignition configs, Flatcar Kernel/Initrd, and Sysext images (`.raw`) + configs (`.conf`).
* **Artifacts**: Directory containing downloaded OS images (Flatcar) and generated configs.

### 2. Target Host (Bare Metal Node)

The physical machine to be provisioned.

* **PXE Client**: NIC boots via network (DHCP provided externally).
* **Flatcar OS**: The operating system loaded into RAM and then installed to disk.
* **Kubeadm**: The tool used to bootstrap the Kubernetes cluster.

## Technologies

* **OS**: Flatcar Container Linux
* **Orchestrator**: Kubernetes (via Kubeadm)
* **CNI/Ingress**: Cilium (ebpf-based, kube-proxy replacement)
* **GitOps**: ArgoCD (App-of-Apps pattern)
* **Storage**: Rook-Ceph (persistent RBD block storage)
* **Config Gen**: Ansible (Jinja2 templates)
* **Serving**: Python (Standard Library + `tftpy`)
