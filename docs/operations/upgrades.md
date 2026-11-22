---
description: "How the OS and Kubernetes get updated on these nodes, and why nothing takes effect until a reboot."
---

# Updates & Upgrades

Three mechanisms update a node, and all three land their changes on disk and
wait for a reboot. A node can be four patch releases behind while reporting
that everything is up to date.

## OS updates

Flatcar downloads OS updates in the background into the passive half of its
A/B partition pair. `locksmithd`, which would normally coordinate the reboot,
is masked in `ansible/templates/butane_node_config.yaml.j2`, because a reboot
must drain the node first. In its place `flatcar-reboot-sentinel.timer` polls
`update_engine_client -status` and touches `/run/reboot-required` once an
update is staged. [Kured](../platform/kured.md) consumes that marker: it
drains, reboots and uncordons, one node at a time and only while Ceph and etcd
are healthy. To move sooner, see [Rebooting a node](nodes.md#rebooting-a-node).

Three alerts in `payload/platform/monitoring/node-update-rules.yaml` watch
that this actually happens, because Trivy scans containers and not the host:
`NodeRebootPending` when a node has carried the marker for longer than its
`for`, `NodeOsVersionDrift` and `NodeKubeletVersionDrift` when the nodes have
run different Flatcar or kubelet versions for longer than theirs.

```bash
ssh core@<node> 'cat /etc/os-release; systemctl status update-engine --no-pager'
```

## Kubernetes

Kubernetes is a [systemd sysext](../concepts.md#systemd-sysexts).
`systemd-sysupdate.timer` runs an update for it on every fire and touches
the same `/run/reboot-required` when a new image is fetched. containerd is
part of the OS image and updates with it.

### Nodes are pinned to a minor series

The sysupdate config points at `https://extensions.flatcar.org/extensions/`,
not at the boot server, and its `MatchPattern` names the major.minor from
`kubernetes_version` in `ansible/inventory.yaml` with only the patch left as
the `@v` wildcard — see `ansible/templates/sysupdate_kubernetes.conf.j2`.

Patch releases inside the series are picked up automatically; the next minor is
not, because an unattended minor jump leaves kubelets ahead of a control plane
that refuses to talk to them. sysext-bakery publishes a floating
`kubernetes.conf` (`kubernetes-@v-%a.raw`) as well, which would stage a minor
kubeadm cannot skip to, on whichever node checks first.
`ansible/templates/sysupdate_kubernetes.conf.j2` renders the pinned file from
the inventory and the Ignition config inlines it, so the source of every later
sysext is reviewed in Git rather than fetched; `make config` also writes it to
`output/http/kubernetes.conf` for running nodes.

```bash
ssh core@<node> 'ls -l /etc/extensions/ /opt/extensions/kubernetes/'
ssh core@<node> cat /etc/sysupdate.kubernetes.d/kubernetes.conf   # confirm the MatchPattern is pinned
```

## Upgrading a minor version deliberately

**kubeadm allows one minor at a time.** v1.34 to v1.37 is three passes through
this procedure, each with its release notes read. A newly provisioned node
skips all of it: it installs `kubernetes_version` directly and gets the pinned
sysupdate config from the boot server.

The order is upstream's: the first control plane, the other control planes,
then the workers, because a kubelet may run older than the API server but never
newer. The sysext swaps kubeadm and kubelet together at the reboot, so the
sysupdate config goes to one group at a time: Kured reboots one node at a
time but in no particular order, and a config pushed to every node at once hands the
workers a new kubelet before the API server has moved.

1. Update `kubernetes_version` in `ansible/inventory.yaml`, and
   `kubernetes_sha256` beside it to the new image's: `make download` refuses
   a file that does not match.
2. Fetch the new sysext and regenerate the Ignition and sysupdate configs.

    ```bash
    make download && make config
    ```

3. Stage the new sysext on the first control plane only. The sysupdate config
   was written once at install time and nothing rewrites it, and the
   boot server is not needed. The sentinel must exist afterwards; stop
   otherwise.

    ```bash
    scp output/http/kubernetes.conf core@<node>:/tmp/kubernetes.conf
    ssh core@<node> sudo mv /tmp/kubernetes.conf /etc/sysupdate.kubernetes.d/kubernetes.conf
    ssh core@<node> sudo systemctl start systemd-sysupdate
    ssh core@<node> ls /run/reboot-required
    ```

4. Let Kured reboot it, as in [Rebooting a node](nodes.md#rebooting-a-node).
   The node must come back `Ready` on the new kubelet version; stop otherwise.

    ```bash
    kubectl get node <node> -w
    ```

5. On that node, run the kubeadm upgrade; the sysext swapped the binaries and
   nothing else. Always pass `--skip-phases addon/kube-proxy`: a cluster built
   before `proxy.disabled` in `kubeadm.yaml.j2` keeps `proxy: {}` in its
   stored `kubeadm-config`, and the upgrade would redeploy `kube-proxy` beside
   Cilium. Always pass `--patches /etc/kubernetes/patches` too: the static
   pods' memory requests and CoreDNS's spread constraint come from that directory and an upgrade rewrites the
   manifests without them otherwise.

    ```bash
    sudo kubeadm upgrade apply <version> --skip-phases addon/kube-proxy --patches /etc/kubernetes/patches
    ```

    It must end with `SUCCESS!` and `kubectl version` must report the new
    server; stop otherwise.

6. Each other control plane in turn: steps 3 and 4, then
   `kubeadm upgrade node` with the same `--patches` flag, which has to run
   the new kubeadm the reboot swapped in and takes the version `upgrade apply`
   stored. `kubectl get nodes` must show the node `Ready`
   before the next one.

    ```bash
    sudo kubeadm upgrade node --patches /etc/kubernetes/patches
    ```

7. Each worker in turn: steps 3 and 4, then `kubeadm upgrade node`, which only
   fetches the new kubelet configuration. Same check before the next.

    ```bash
    sudo kubeadm upgrade node
    ```

!!! note
    Renovate keeps `kubernetes_version`, `flatcar_version` and `syslinux_version` current in `ansible/inventory.yaml`; which bumps automerge is in [Automerge policy](../development/maintenance.md#automerge-policy).

## Platform components

Everything in `payload/`, ArgoCD included, is upgraded by ArgoCD when a
Renovate bump merges. Cilium, the Gateway API CRDs and ArgoCD are installed
from the same `targetRevision` before ArgoCD exists — see [Version pins](../architecture/gitops.md#version-pins).

## Control-plane certificates

kubeadm issues the control-plane certificates for one year and renews them only
during `kubeadm upgrade`. Patch updates arrive as a sysext and never run
kubeadm, and a minor is [a manual procedure](#upgrading-a-minor-version-deliberately),
so a year without one takes the API servers, the etcd peers and the front
proxy down together. The kubelet certificates are the exception: the serving
certificate is rotated through [kubelet-csr-approver](../platform/metrics-server.md)
and the client certificate by the kubelet itself.

`payload/platform/monitoring/control-plane-certificate-rules.yaml` warns thirty days ahead on
the client certificates the API server sees, which kubeadm issues together
with the serving ones; the chart's own `KubeClientCertificateExpiration` takes
over at seven days and one day. The renewal is done by hand, once a year or
when the alert fires, on every control plane in turn:

1. Read the remaining lifetime; every line but the CA should have well over
   thirty days.

    ```bash
    ssh core@<control plane> sudo kubeadm certs check-expiration
    ```

2. Renew everything kubeadm issued on that node. The CA is not touched.

    ```bash
    ssh core@<control plane> sudo kubeadm certs renew all
    ```

3. Restart the static pods so they load the new certificates: move the
   manifests out, wait until `crictl` lists none of the four, move them back.
   One node at a time, since the API server and etcd on it are down in
   between.

    ```bash
    ssh core@<control plane> 'sudo mkdir -p /root/manifests-stopped && sudo mv /etc/kubernetes/manifests/*.yaml /root/manifests-stopped/ && until [ -z "$(sudo crictl ps -q --name "kube-apiserver|kube-controller-manager|kube-scheduler|etcd")" ]; do sleep 2; done && sudo mv /root/manifests-stopped/*.yaml /etc/kubernetes/manifests/'
    ```

4. After the last node, refresh the admin kubeconfig: `admin.conf` was renewed
   too, and the copy in `output/kubeconfig` carries the old client certificate.

    ```bash
    make kubeconfig
    ```
