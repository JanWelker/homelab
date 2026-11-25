# Control Plane VIP

The API server is reached through a virtual IP held by [kube-vip](<https://kube-vip.io/>), not through one named node: whichever control-plane node wins the leader election answers on `control_plane_vip` from `ansible/inventory.yaml`, and another takes over if it goes away. The address is fixed at `kubeadm init` — see [Known Limitations](<https://homelab.wlkr.ch/architecture/limitations/index.md>); a cluster built before the VIP is [migrated in place](<#migrating-a-cluster-built-without-a-vip>).

## How it is wired

| Piece | Where |
| --- | --- |
| VIP address and interface | `control_plane_vip`, `control_plane_vip_interface` in `ansible/inventory.yaml` |
| Bootstrap static pod | Written by Ignition to `/etc/kubernetes/manifests/kube-vip.yaml` on control-plane nodes, at `kube_vip_version`, because it must run before `kubeadm init` writes the endpoint into the certificates |
| Running kube-vip | DaemonSet in `payload/platform/kube-vip/`, synced by ArgoCD; its image is tracked by Renovate |
| Cluster endpoint | `controlPlaneEndpoint` in the generated kubeadm config, and every `kubeadm join`, from `control_plane_endpoint_host` in `ansible/inventory.yaml` |
| Cilium's API address | `k8sServiceHost` in `payload/platform/cilium/values.yaml` |
| Kubelet node address | `--node-ip` in the kubelet drop-in of `ansible/templates/butane_node_config.yaml.j2`, so the kubelet never reports the VIP on the leader's interface as InternalIP |

kube-vip runs in ARP mode with leader election, advertising a `/32`, with `svc_enable: "false"`: LoadBalancer services belong to Cilium's L2 announcements, and two components answering ARP for the same range produces intermittent, host-dependent failures.

## Design

The static pod is only the bootstrap: once ArgoCD syncs `payload/platform/kube-vip/`, a DaemonSet takes over, so upgrading kube-vip is a merged PR rather than a file rewritten on each node. kube-vip's chart is not used: it has no hook for the `adopt` init container below. A DaemonSet can hold the VIP here because the control-plane kubelets use their own node's API server (`server:` in `/etc/kubernetes/kubelet.conf`) and so does kube-vip (`KUBERNETES_SERVICE_HOST` is the host IP, which is in the certificate SANs), so both start after a full power loss without the VIP or Cilium. `kube_vip_version` in the inventory is therefore the bootstrap version only, and the VIP settings in the inventory and `daemonset.yaml` must be kept in step. kube-vip's ServiceAccount may only get and update its own Lease, and the Application does not prune and its objects carry `Delete=false`, because removing them takes the API away from the workers and from every kubeconfig.

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
| Liveness probe on `/metrics` | Every kube-vip serves it, leader or not, so a hung process holding the Lease is restarted instead of keeping the VIP dead until another node's renew fails too; the long failure window keeps a slow API server from recycling a healthy pod |

### The `adopt` trade-off

`adopt` and its writable hostPath stay after the handover: they do nothing on an adopted node, but a reinstalled control plane gets the static manifest back from Ignition and needs adoption again, or two kube-vips contend for the VIP. The cost is a container on every pod start that can delete the static pod manifests, limited to the pinned image.

> [!CAUTION]
>
> **If the DaemonSet pods do not come up**
>
> The static manifests are already gone, so nothing holds the VIP, and every kubeconfig and Cilium's `k8sServiceHost` point at it. Running pods keep their networking, but no Cilium agent sees the API server until the VIP is back. Point `kubectl` at a control-plane node's `ansible_host` from `ansible/inventory.yaml` with `--server https://<address>:6443`, write `/etc/kubernetes/manifests/kube-vip.yaml` back onto one control-plane node from `ansible/templates/butane_node_config.yaml.j2` so that the VIP answers again, then read `kubectl -n kube-system logs ds/kube-vip -c kube-vip` and fix the DaemonSet in Git. The static pod holds the VIP only while the broken DaemonSet pod merely restarts; if that pod is deleted or recreated, `adopt` removes the file again.

> [!NOTE]
>
> Since Kubernetes 1.29, `admin.conf` is not usable until `kubeadm init` finishes, so the bootstrap unit points kube-vip's `hostPath` at `super-admin.conf` during init and moves it back afterwards. Inside the container the file stays at `/etc/kubernetes/admin.conf`.

## Endpoint name

The next cluster build uses a DNS name as `controlPlaneEndpoint`, so moving the VIP later no longer means rewriting every node's kubeconfig; `certSANs` still needs regenerating for the new address. The running cluster keeps the bare VIP: there is no migration, because its certificates and stored configuration do not carry a name.

| Piece | With a name | Why |
| --- | --- | --- |
| `/etc/hosts` from Ignition, every node | Resolves the name to the VIP | The API endpoint must not depend on LAN DNS |
| `apiServer.certSANs` | Holds the VIP | Cilium's `k8sServiceHost` and `make kubeconfig` keep using the VIP |
| `make kubeconfig` | Writes the VIP as `server` | Only the nodes resolve the name |
| `make config`, `make reinstall` | Refuse a name the answering cluster does not publish | A node rendered with it fails certificate verification on join, after its disk is wiped |

To build with a name:

1. Set `control_plane_endpoint_host` in `ansible/inventory.yaml` to the name, before the first `make config` of the build, and keep it there.

   ```yaml
   control_plane_endpoint_host: k8s-api.<lan domain>
   ```
2. Continue with the [Quickstart](<https://homelab.wlkr.ch/quickstart/index.md>). Everything else follows from that variable.

Once the first control plane is up, check the nodes and the cluster agree.

```bash
kubectl -n kube-public get configmap cluster-info -o jsonpath='{.data.kubeconfig}' | grep server
```

## Choosing the address

A free address on the nodes' subnet, outside any DHCP range, and distinct from the Cilium LoadBalancer pools. Nothing validates this: a collision leaves the API server unreachable, or reachable only from some machines.

## Migrating a cluster built without a VIP

A cluster provisioned before the VIP has its first control plane's address in the API server certificates, so this is no config change to sync.

### Rebuild (simplest)

Reprovision from the [Quickstart](<https://homelab.wlkr.ch/quickstart/index.md>); the VIP is in place from `kubeadm init` onwards. Restore state per [Backups & Recovery](<https://homelab.wlkr.ch/operations/backups/index.md>).

### In place

One node at a time, with a second terminal holding a working kubeconfig, since several steps can lock you out.

1. Confirm the address is free.

   ```bash
   ping -c2 <vip>        # must not answer
   arping -c2 <vip>      # from a node on the segment
   ```
2. Add the VIP under `apiServer.certSANs` in the stored `ClusterConfiguration`.

   ```bash
   kubectl -n kube-system edit configmap kubeadm-config
   ```
3. On **each** control-plane node, regenerate the API server certificate from the config `make config` rendered for it, which carries the VIP and the join token, hence the `rm`; kubeadm does not read the edited ConfigMap. The SANs must list the VIP.

   ```bash
   scp output/tmp/kubeadm/kubeadm-config-<node>.yaml core@<node>:/tmp/kubeadm.yaml
   ssh core@<node> 'cd /etc/kubernetes/pki && sudo mv apiserver.crt apiserver.crt.bak && sudo mv apiserver.key apiserver.key.bak && sudo kubeadm init phase certs apiserver --config /tmp/kubeadm.yaml; rm /tmp/kubeadm.yaml'
   ssh core@<node> sudo cat /etc/kubernetes/pki/apiserver.crt | openssl x509 -noout -text | grep -A1 'Subject Alternative Name'
   ```
4. Deploy the kube-vip static pod to each control-plane node, generated from the template's values or copied from a node provisioned with the new configuration, and verify the VIP answers.

   ```bash
   curl -k https://<vip>:6443/healthz
   ```
5. Repoint the cluster: update `controlPlaneEndpoint` in the `kubeadm-config` ConfigMap, then on every node rewrite the server address in `/etc/kubernetes/*.conf` and `/var/lib/kubelet/kubeconfig` and restart the kubelet.
6. **Only now** change `k8sServiceHost` in `payload/platform/cilium/values.yaml` to the VIP and commit. The merge rolls every agent, so the VIP must answer before it lands; watch the rollout.

   ```bash
   kubectl -n kube-system rollout status ds/cilium
   ```
7. Re-fetch your kubeconfig so it uses the VIP.

   ```bash
   make kubeconfig
   ```

> [!CAUTION]
>
> **Danger**
>
> Cilium replaces kube-proxy, so if `k8sServiceHost` names an address that is not answering, every Cilium pod loses the API server and the cluster's networking goes with it, including the networking you would fix it through. Do not commit step 6 until `curl -k https://<vip>:6443/healthz` succeeds.

## Verifying

```bash
# which node currently holds the VIP
kubectl -n kube-system get lease plndr-cp-lock -o jsonpath='{.spec.holderIdentity}'; echo
kubectl -n kube-system get pods -l app.kubernetes.io/name=kube-vip -o wide

# the endpoint your kubeconfig actually uses
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}'; echo
```

Test failover once: have Kured reboot the leader per [Rebooting a node](<https://homelab.wlkr.ch/operations/nodes/index.md#rebooting-a-node>) and confirm `kubectl` keeps working after a few seconds.
