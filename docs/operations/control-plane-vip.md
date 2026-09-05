---
description: "The kube-vip virtual IP that fronts the API servers, and how to migrate a cluster that was built without one."
---

# Control Plane VIP

The Kubernetes API server is reached through a virtual IP held by
[kube-vip](https://kube-vip.io/), not through one named node. Whichever
control-plane node wins the leader election answers on
`control_plane_vip` from `ansible/inventory.yaml`; if it goes away, another
takes the address over.

This removes the dependency on a single control-plane node for cluster
*access* — see the history in
[Known Limitations](../architecture/limitations.md#single-api-server-endpoint).

## How it is wired

| Piece | Where |
| --- | --- |
| VIP address and interface | `control_plane_vip`, `control_plane_vip_interface` in `ansible/inventory.yaml` |
| kube-vip version | `kube_vip_version`, tracked by Renovate in the `Core Infrastructure` group |
| Static pod manifest | Written by Ignition to `/etc/kubernetes/manifests/kube-vip.yaml` on control-plane nodes only |
| Cluster endpoint | `controlPlaneEndpoint` in the generated kubeadm config, and every `kubeadm join` command |
| Cilium's API address | `k8sServiceHost` in `payload/platform/cilium/values.yaml` |

kube-vip runs in ARP mode with leader election, advertising a `/32`. It is
deliberately configured with `svc_enable: "false"` — LoadBalancer services
belong to Cilium's L2 announcements, and both claiming them would conflict.

The manifest is placed by Ignition rather than applied afterwards because it has
to be running before `kubeadm init` writes the endpoint into the cluster's
certificates.

!!! note
    Since Kubernetes 1.29, `admin.conf` is not usable until `kubeadm init`
    finishes, so the bootstrap unit points kube-vip's `hostPath` at
    `super-admin.conf` for the duration of init and moves it back afterwards.
    Only the host path changes; inside the container the file stays at
    `/etc/kubernetes/admin.conf`.

## Choosing the address

It must be a free address on the nodes' subnet, outside any DHCP range, and
distinct from the Cilium LoadBalancer pools (`10.9.2.248` and `10.9.2.249`).
Nothing validates this — a collision shows up as an unreachable API server after
provisioning.

## Migrating a cluster built without a VIP

A cluster provisioned before this change has its first control-plane node's
address baked into the API server certificates, so this is not a config change
you can simply sync. Two options:

### Rebuild (simplest)

Reprovision from the [Quickstart](../quickstart.md). The VIP is in place from
`kubeadm init` onwards and no migration is needed. Restore state per
[Backups & Recovery](backups.md).

### In place

Only worth it if rebuilding is not an option. Work on one node at a time and
keep a second terminal open with a working kubeconfig.

1. Confirm the address is free:

    ```bash
    ping -c2 <vip>        # must not answer
    arping -c2 <vip>      # from a node on the segment
    ```

2. Add the VIP to the API server certificate SANs. Edit the `ClusterConfiguration`
   stored in the cluster and add it under `apiServer.certSANs`:

    ```bash
    kubectl -n kube-system edit configmap kubeadm-config
    ```

3. Regenerate the API server certificate on **each** control-plane node:

    ```bash
    ssh core@<node>
    sudo mv /etc/kubernetes/pki/apiserver.crt /etc/kubernetes/pki/apiserver.crt.bak
    sudo mv /etc/kubernetes/pki/apiserver.key /etc/kubernetes/pki/apiserver.key.bak
    sudo kubeadm init phase certs apiserver
    sudo crictl ps | grep kube-apiserver   # confirm it restarted
    ```

4. Deploy the kube-vip static pod to each control-plane node. Generate it from
   the same values the template uses, or copy
   `/etc/kubernetes/manifests/kube-vip.yaml` from a node reprovisioned with the
   new configuration. Verify the VIP answers:

    ```bash
    curl -k https://<vip>:6443/healthz
    ```

5. Repoint the cluster at it. Update `controlPlaneEndpoint` in the
   `kubeadm-config` ConfigMap, then on every node rewrite the server address in
   `/etc/kubernetes/*.conf` and `/var/lib/kubelet/kubeconfig` and restart the
   kubelet.

6. **Only now** switch Cilium. Change `k8sServiceHost` in
   `payload/platform/cilium/values.yaml` to the VIP, commit, and let ArgoCD sync.
   Restart the Cilium DaemonSet and confirm every pod reconnects:

    ```bash
    kubectl -n kube-system rollout restart ds/cilium
    kubectl -n kube-system rollout status ds/cilium
    ```

7. Re-fetch your kubeconfig so it uses the VIP:

    ```bash
    make kubeconfig
    ```

!!! danger
    Step 6 is the one that bites. Cilium replaces kube-proxy, so if
    `k8sServiceHost` names an address that is not answering, every Cilium pod
    loses the API server and the cluster's networking goes with it. Do not
    commit that change until `curl -k https://<vip>:6443/healthz` succeeds.

## Verifying

```bash
# which node currently holds the VIP
kubectl -n kube-system get lease kube-vip -o jsonpath='{.spec.holderIdentity}'; echo
kubectl -n kube-system get pods -l name=kube-vip -o wide

# the endpoint your kubeconfig actually uses
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'; echo
```

To test failover, reboot the leader per
[Rebooting a node](index.md#rebooting-a-node) and confirm `kubectl` keeps
working after a few seconds.
