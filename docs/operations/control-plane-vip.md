---
description: "The kube-vip virtual IP that fronts the API servers, and how to migrate a cluster that was built without one."
---

# Control Plane VIP

The API server is reached through a virtual IP held by
[kube-vip](https://kube-vip.io/), not through one named node: whichever
control-plane node wins the leader election answers on `control_plane_vip`
from `ansible/inventory.yaml`, and another takes over if it goes away. See
[Known Limitations](../architecture/limitations.md)
for a cluster whose certificates predate the VIP.

## How it is wired

| Piece | Where |
| --- | --- |
| VIP address and interface | `control_plane_vip`, `control_plane_vip_interface` in `ansible/inventory.yaml` |
| Bootstrap static pod | Written by Ignition to `/etc/kubernetes/manifests/kube-vip.yaml` on control-plane nodes, at `kube_vip_version`, because it must run before `kubeadm init` writes the endpoint into the certificates |
| Running kube-vip | DaemonSet in `payload/platform/kube-vip/`, synced by ArgoCD; its image is tracked by Renovate |
| Cluster endpoint | `controlPlaneEndpoint` in the generated kubeadm config, and every `kubeadm join` |
| Cilium's API address | `k8sServiceHost` in `payload/platform/cilium/values.yaml` |

kube-vip runs in ARP mode with leader election, advertising a `/32`, with
`svc_enable: "false"`: LoadBalancer services belong to Cilium's L2
announcements, and two components answering ARP for the same range produces
intermittent, host-dependent failures.

## Design

The static pod is only the bootstrap: once ArgoCD syncs
`payload/platform/kube-vip/`, a DaemonSet takes over, so upgrading kube-vip is
a merged PR rather than a file rewritten on each node. A DaemonSet can hold the
VIP here because the control-plane kubelets use their own node's API server
(`server:` in `/etc/kubernetes/kubelet.conf`) and so does kube-vip
(`KUBERNETES_SERVICE_HOST` is the host IP, which is in the certificate SANs),
so both start after a full power loss without the VIP or Cilium.
`kube_vip_version` in the inventory is therefore the bootstrap version only,
and the VIP settings in the inventory and `daemonset.yaml` must be kept in
step. The first sync replaces all three static pods at once and drops the VIP
for a few seconds; later updates roll one node at a time. kube-vip's
ServiceAccount may only get and update its own Lease, and the Application does
not prune and its objects carry `Delete=false`, because removing them takes
the API away from the workers and from every kubeconfig.

### The handover

| Detail | Why |
| --- | --- |
| `pull` init container | Fetches the kube-vip image first, so the gap between old and new process has no registry round trip. Keep its image in step with the main container |
| `adopt` init container | Deletes the static manifest and waits for the static kube-vip to release `:2112`; both use the node name as leader identity and the same `plndr-cp-lock` Lease, so they must never run side by side |
| `/etc/kubernetes/manifests` mounted as a directory | A hostPath mounted as a single file is a bind mount and cannot be removed |
| 120 s timeout in `adopt` | The kubelet rescans manifests every 20 s; giving up leaves a visible stuck `Init` rather than a second kube-vip on the port |
| `maxUnavailable: 1`, no surge | An old and a new pod would fight over `:2112` and the lease |
| Tolerates every taint | A NotReady or pressured control-plane node is when the VIP has to move |
| Capabilities identical to the static pod | The first sync replaces all three at once; untested hardening takes the VIP down everywhere |

!!! danger "If the DaemonSet pods do not come up"
    The static manifests are already gone, so nothing holds the VIP. As long as Cilium's `k8sServiceHost` names a node rather than the VIP, Cilium and ArgoCD keep working. Point `kubectl` at a node with `--server https://10.9.2.1:6443`, read `kubectl -n kube-system logs ds/kube-vip -c kube-vip`, and fix the DaemonSet in Git. If that cannot wait, write `/etc/kubernetes/manifests/kube-vip.yaml` back onto one control-plane node from `ansible/templates/butane_node_config.yaml.j2`. That holds while the broken pod merely restarts; if the pod is deleted or recreated, `adopt` removes the file again.

!!! note
    Since Kubernetes 1.29, `admin.conf` is not usable until `kubeadm init` finishes, so the bootstrap unit points kube-vip's `hostPath` at `super-admin.conf` during init and moves it back afterwards. Inside the container the file stays at `/etc/kubernetes/admin.conf`.

## Choosing the address

A free address on the nodes' subnet, outside any DHCP range, and distinct from
the Cilium LoadBalancer pools. Nothing validates this: a collision shows up as
an API server unreachable after provisioning, or reachable from some machines
and not others depending on whose ARP cache won.

## Migrating a cluster built without a VIP

A cluster provisioned before the VIP has its first control-plane node's
address baked into the API server certificates, so this is not a config change
you can sync.

### Rebuild (simplest)

Reprovision from the [Quickstart](../quickstart.md); the VIP is in place from
`kubeadm init` onwards. Restore state per [Backups & Recovery](backups.md).

### In place

One node at a time, with a second terminal holding a working kubeconfig,
because several steps can leave you unable to open a new one.

1. Confirm the address is free.

    ```bash
    ping -c2 <vip>        # must not answer
    arping -c2 <vip>      # from a node on the segment
    ```

2. Add the VIP under `apiServer.certSANs` in the stored `ClusterConfiguration`.

    ```bash
    kubectl -n kube-system edit configmap kubeadm-config
    ```

3. Regenerate the API server certificate on **each** control-plane node.

    ```bash
    ssh core@<node>
    sudo mv /etc/kubernetes/pki/apiserver.crt /etc/kubernetes/pki/apiserver.crt.bak
    sudo mv /etc/kubernetes/pki/apiserver.key /etc/kubernetes/pki/apiserver.key.bak
    sudo kubeadm init phase certs apiserver
    sudo crictl ps | grep kube-apiserver   # confirm it restarted
    ```

4. Deploy the kube-vip static pod to each control-plane node, generated from
   the template's values or copied from a node provisioned with the new
   configuration, and verify the VIP answers.

    ```bash
    curl -k https://<vip>:6443/healthz
    ```

5. Repoint the cluster: update `controlPlaneEndpoint` in the `kubeadm-config`
   ConfigMap, then on every node rewrite the server address in
   `/etc/kubernetes/*.conf` and `/var/lib/kubelet/kubeconfig` and restart the
   kubelet.

6. **Only now** change `k8sServiceHost` in `payload/platform/cilium/values.yaml`
   to the VIP and commit. The merge rolls every agent, so the VIP must
   answer before it lands; watch the rollout.

    ```bash
    kubectl -n kube-system rollout status ds/cilium
    ```

7. Re-fetch your kubeconfig so it uses the VIP.

    ```bash
    make kubeconfig
    ```

!!! danger
    Cilium replaces kube-proxy, so if `k8sServiceHost` names an address that is not answering, every Cilium pod loses the API server and the cluster's networking goes with it, including the networking you would fix it through. Do not commit step 6 until `curl -k https://<vip>:6443/healthz` succeeds.

## Verifying

```bash
# which node currently holds the VIP
kubectl -n kube-system get lease plndr-cp-lock -o jsonpath='{.spec.holderIdentity}'; echo
kubectl -n kube-system get pods -l app.kubernetes.io/name=kube-vip -o wide

# the endpoint your kubeconfig actually uses
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'; echo
```

Test failover once, deliberately: reboot the leader per
[Rebooting a node](nodes.md#rebooting-a-node) and confirm `kubectl` keeps
working after a few seconds.
