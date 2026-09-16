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
*access* — see
[Known Limitations](../architecture/limitations.md#single-api-server-endpoint)
for what still applies to a cluster whose certificates predate the VIP.

The alternative, which almost every first cluster does, is to name one node in
the kubeconfig and quietly promote it to Most Important Machine. It works
perfectly until that machine needs a reboot, at which point you discover that
"highly available control plane" meant "three copies of etcd behind one
hostname".

## How it is wired

| Piece | Where |
| --- | --- |
| VIP address and interface | `control_plane_vip`, `control_plane_vip_interface` in `ansible/inventory.yaml` |
| Bootstrap static pod | Written by Ignition to `/etc/kubernetes/manifests/kube-vip.yaml` on control-plane nodes only, at `kube_vip_version` |
| Running kube-vip | DaemonSet in `payload/platform/kube-vip/`, synced by ArgoCD; its image is tracked by Renovate |
| Cluster endpoint | `controlPlaneEndpoint` in the generated kubeadm config, and every `kubeadm join` command |
| Cilium's API address | `k8sServiceHost` in `payload/platform/cilium/values.yaml` |

kube-vip runs in ARP mode with leader election, advertising a `/32`. It is
deliberately configured with `svc_enable: "false"` — LoadBalancer services
belong to Cilium's L2 announcements, and two components fighting over who gets
to answer an ARP request produces the kind of intermittent, host-dependent
weirdness that eats an entire evening.

The manifest is placed by Ignition rather than applied afterwards because it has
to be running before `kubeadm init` writes the endpoint into the cluster's
certificates. Chicken, egg, static pod.

## Adoption by ArgoCD

A static pod only changes when someone rewrites the file on the node, which in
practice means reinstalling it. So the static pod is only the bootstrap: once
ArgoCD syncs `payload/platform/kube-vip/`, a DaemonSet on the control-plane
nodes takes over, and upgrading kube-vip is a merged PR like any other chart.

On each node, the DaemonSet's `adopt` init container deletes
`/etc/kubernetes/manifests/kube-vip.yaml` and waits for the static kube-vip to
release `:2112` before the new one starts. Both use the node name as their
leader-election identity and the same `plndr-cp-lock` Lease, so the two must
never run side by side on one node. Where the file is already gone, the init
container does nothing.

A DaemonSet is usually the wrong home for the VIP, because a kubelet that
reaches the API through the VIP cannot fetch the pod that would bring it up.
That does not apply here: the control-plane kubelets use their own node's API
server (check `server:` in `/etc/kubernetes/kubelet.conf`), so they still start
kube-vip after a full power loss. kube-vip itself talks to the node's API server
too, not to the `kubernetes` Service, which would need Cilium first.

What that changes:

- `kube_vip_version` in the inventory is the bootstrap version only. Bump
  the image in `daemonset.yaml` to upgrade a running cluster.
- The first sync replaces all three static pods at once, so the VIP drops for a
  few seconds. Later updates roll one node at a time.
- The VIP settings live in two places, the inventory and `daemonset.yaml`. Keep
  them in step.
- kube-vip no longer uses `admin.conf`. Its `kube-vip` ServiceAccount may only
  get and update its own Lease.
- The Application does not prune, and its objects carry `Delete=false`: removing
  them takes the API away from the workers and from every kubeconfig.

!!! danger "If the DaemonSet pods do not come up"
    The static manifests are already gone, so nothing holds the VIP. As long as Cilium's `k8sServiceHost` names a node rather than the VIP, Cilium and ArgoCD keep working. Point `kubectl` at a node with `--server https://10.9.2.1:6443`, read `kubectl -n kube-system logs ds/kube-vip -c kube-vip`, and fix the DaemonSet in Git. If that cannot wait, write `/etc/kubernetes/manifests/kube-vip.yaml` back onto one control-plane node from `ansible/templates/butane_node_config.yaml.j2`. That holds while the broken pod merely restarts, since init containers do not rerun then; if the pod is deleted or recreated, `adopt` removes the file again.

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
provisioning, or worse, as an API server that works from some machines and not
others depending on whose ARP cache won.

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
keep a second terminal open with a working kubeconfig — not as a nicety, but
because several of the steps below can leave you unable to open a new one.

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
    Step 6 is the one that bites. Cilium replaces kube-proxy, so if `k8sServiceHost` names an address that is not answering, every Cilium pod loses the API server and the cluster's networking goes with it — including, delightfully, the networking you were using to fix it. Do not commit that change until `curl -k https://<vip>:6443/healthz` succeeds.

## Verifying

```bash
# which node currently holds the VIP
kubectl -n kube-system get lease plndr-cp-lock -o jsonpath='{.spec.holderIdentity}'; echo
kubectl -n kube-system get pods -l app.kubernetes.io/name=kube-vip -o wide

# the endpoint your kubeconfig actually uses
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'; echo
```

To test failover, reboot the leader per
[Rebooting a node](nodes.md#rebooting-a-node) and confirm `kubectl` keeps
working after a few seconds. Do this once, deliberately, on a quiet afternoon.
Untested failover is not failover; it is a hypothesis.
