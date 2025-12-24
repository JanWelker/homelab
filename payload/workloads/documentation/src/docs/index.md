# Bare Metal K8s Deployment

![Homelab Logo](assets/images/logo.png){ align=right width=150 }

This project automates the deployment of a bare metal Kubernetes cluster using
Flatcar Container Linux and Kubeadm.

## Prerequisites

* **Python 3**: For configuration and boot server.
* **Butane**: For transpiling config files (`brew install butane` or similar).
* **SSH Key**: An Ed25519 SSH key at `~/.ssh/id_ed25519.pub` (or modify `ansible/templates/butane_config.yaml.j2`).
* **External DHCP Server**: Configured to point `next-server` (Option 66) to
  this host's IP.
  * **BIOS**: set `filename` (Option 67) to `lpxelinux.0` (Required for HTTP
    boot support).
  * **UEFI**: set `filename` (Option 67) to `syslinux.efi`.

## Setup

1. **Initialize Environment**:
    Create a virtual environment and install dependencies:

    ```bash
    make setup
    source .venv/bin/activate
    ```

2. **Configure Inventory**: Edit `ansible/inventory.yml` to define your target
    nodes (MAC addresses and Roles) and set versions (`kubernetes_version`,
    `containerd_version`).
    * **Cilium**: Installed via Helm (version managed in `Makefile`). Configured
      to replace `kube-proxy` entirely and use **WireGuard** for transparent
      network encryption.
3. **Download Artifacts**:

    ```bash
    make download
    ```

    *Downloads Flatcar artifacts, Syslinux, and Systemd Sysext images
    (Kubernetes, Containerd) to `output/http`.*

4. **Generate Configurations**:

    ```bash
    make config
    ```

    *Artifacts will be generated in `output/http` (Ignition) and `output/tftp`
    (PXE). Note: The NVMe disk is partitioned into 50GB for containerd and the
    remaining space for Rook-Ceph storage.*

5. **Start Boot Server** (Requires sudo for port 69):

    ```bash
    make serve
    ```

6. **Boot Machines**:
    Power on your bare metal nodes. They will PXE boot, install Flatcar, and reboot.
    * **Note**: The cluster will come up in a `NotReady` state initially because
      no CNI is installed.

7. **Retrieve Kubeconfig**:
    Once the control plane node responds to SSH (or is pingable), retrieve the
    admin kubeconfig:

    ```bash
    make kubeconfig
    ```

    *Optional: Install to local machine (will not overwrite existing config):*

    ```bash
    mkdir -p ~/.kube
    cp -n output/kubeconfig ~/.kube/config
    ```

8. **Install Core Components** (CRITICAL):
    **Untaint Control Plane** (Optional, for single-node clusters):

    ```bash
    make untaint
    ```

    **Re-taint Control Plane** (When worker nodes join):
    If you add worker nodes later, you should re-apply the scheduling taints to
    the control plane to ensure workloads are scheduled correctly:

    ```bash
    make taint
    ```

    With `output/kubeconfig` in place (`kubeadm` likely finished):

    ```bash
    make install-core
    ```

    * Installs **Cilium** (CNI, Ingress, L2 Announcements) via Helm.
    * **Removes** `kube-proxy` to resolve IPVS conflicts.
    * Installs **Cert-Manager** (for ACME TLS).
    * *The node should become `Ready` after this step.*

9. **Post-Installation**:

    * **Deploy ArgoCD**:

        ```bash
        make install-argo
        ```

    * **Bootstrap GitOps** (App-of-Apps):

        ```bash
        make bootstrap-apps
        ```

        *This applies the parent applications (workloads, platform, gitops)
        which enable ArgoCD to manage all applications from Git.*
