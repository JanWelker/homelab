# Flatcar Kubernetes Homelab

A declarative, GitOps-driven bare metal Kubernetes cluster using Flatcar Container Linux.

## Documentation

Full documentation is available at: **[https://docs.k8s.wlkr.ch](https://docs.k8s.wlkr.ch)**

The source code for the documentation can be found in `payload/workloads/documentation/src`.

## Quickstart

### Prerequisites

- Python 3.x
- Ansible
- `tftpy` (if running boot server directly)

### Bootstrap Deployment

1. **Configure Inventory**: Edit `ansible/inventory.yml` with your node details (MAC addresses, IPs).
2. **Generate Configs**:

    ```bash
    cd ansible
    ansible-playbook playbooks/config.yml
    ```

3. **Start Boot Server**:

    ```bash
    cd boot_server
    sudo python3 serve.py
    ```

4. **Boot Nodes**: Turn on your bare metal nodes. They should PXE boot into Flatcar Container Linux and install themselves.

### Accessing the Cluster

Once the control plane is ready, retrieve the kubeconfig:

```bash
cd ansible
ansible-playbook playbooks/kubeconfig.yml
export KUBECONFIG=$(pwd)/../output/kubeconfig/admin.conf
kubectl get nodes
```
