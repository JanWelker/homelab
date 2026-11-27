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

The alerts in `payload/platform/monitoring/node-update-rules.yaml` watch
that this happens, because Trivy scans containers and not the host:
`NodeRebootPending` when a node carries the marker too long,
`NodeOsVersionDrift` and `NodeKubeletVersionDrift` when the nodes disagree, and
`ControlPlaneVersionLag` when the kubelets run a version no API server runs.

```bash
make os-release uptime
ssh core@<node> systemctl status update-engine --no-pager
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
that refuses to talk to them. sysext-bakery's floating
`kubernetes.conf` would stage that minor on whichever node checks first.
The Ignition config inlines the rendered file; a running node gets a new one
by [reprovision](nodes.md#reprovisioning-a-node).

```bash
ssh core@<node> 'ls -l /etc/extensions/ /opt/extensions/kubernetes/'
ssh core@<node> cat /etc/sysupdate.kubernetes.d/kubernetes.conf   # confirm the MatchPattern is pinned
```

### Patch releases

A patch sysext swaps only kubelet and kubeadm. The control-plane static pods
keep their images until kubeadm moves them; workers Kured reboots
first run the newer kubelet meanwhile, which the skew policy allows within a minor.

1. Wait until every control plane reports the new kubelet.

    ```bash
    kubectl get nodes -l node-role.kubernetes.io/control-plane
    ```

2. Upgrade the control plane. `ansible/playbooks/kube-upgrade.yaml` refuses a
   minor or a control plane still on the old sysext, runs `upgrade apply` on
   the first node ahead of the version stored in `kubeadm-config`,
   `upgrade node` on the rest, and renews the control-plane certificates on
   the way.

    ```bash
    make kube-upgrade
    ```

3. `kubectl version` must report the new server. Merge Renovate's
   `kubernetes_version` bump, or a new node joins on the old patch.

## Upgrading a minor version deliberately

**kubeadm allows one minor at a time.** v1.34 to v1.37 is three passes through
this procedure, each with its release notes read. A newly provisioned node
installs `kubernetes_version` directly.

The order is upstream's: the first control plane, the other control planes,
then the workers, because a kubelet may run older than the API server but never
newer. The new sysupdate config goes to one node at a time, since Kured
reboots in no particular order.

1. Update `kubernetes_version` in `ansible/inventory.yaml`, and
   `kubernetes_sha256` beside it to the new image's: `make download` refuses
   a file that does not match.
2. Fetch the new sysext for new nodes and regenerate the configs, including
   each node's `-reprovision-minor` variant.

    ```bash
    make download && make config
    ```

3. Reprovision the first control plane with `ALLOW_MINOR=1`: Ignition writes
   the new minor's sysext and sysupdate config in place of the old ones, so
   one reboot brings the new kubelet.

    ```bash
    make reprovision LIMIT=<node> ALLOW_MINOR=1
    ```

4. Let [Kured](nodes.md#rebooting-a-node) reboot it. The node must come back
   `Ready` on the new kubelet version and `current` in `make node-drift`;
   stop otherwise.

    ```bash
    kubectl get node <node> -w
    ```

5. Upgrade it. On the first control plane this is `kubeadm upgrade apply`,
   and `kubectl version` must then report the new server; on every later
   node it is `upgrade node`, which follows the stored version. Both skip
   the `kube-proxy` and CoreDNS addons, which Cilium and
   [Argo CD](../platform/coredns.md) own.

    ```bash
    make kube-upgrade LIMIT=<node> ALLOW_MINOR=1
    ```

6. Each other control plane, then each worker, in turn: steps 3 to 5. The
   node must be `Ready` before the next.

!!! note
    Renovate keeps `kubernetes_version`, `flatcar_version` and `syslinux_version` current in `ansible/inventory.yaml`; which bumps automerge is in [Automerge policy](../development/maintenance.md#automerge-policy).

## Platform components

Everything in `payload/`, ArgoCD included, is upgraded by ArgoCD when a
Renovate bump merges. Cilium, the Gateway API CRDs and ArgoCD are installed
from the same `targetRevision` before ArgoCD exists — see [Version pins](../architecture/gitops.md#version-pins).

## Control-plane certificates

kubeadm issues the control-plane certificates for one year and renews them only
during `kubeadm upgrade`, so a year without a [patch](#patch-releases) or
[minor](#upgrading-a-minor-version-deliberately) upgrade takes the API
servers, the etcd peers and the front proxy down together. The kubelet certificates are the exception: the serving
certificate is rotated through [kubelet-csr-approver](../platform/metrics-server.md)
and the client certificate by the kubelet itself.

`payload/platform/monitoring/control-plane-certificate-rules.yaml` warns thirty days ahead on
the client certificates the API server sees, which kubeadm issues together
with the serving ones; the chart's own `KubeClientCertificateExpiration` takes
over at seven days and one day. Without an upgrade in the year, renew them
when the alert fires:

1. Renew on every control plane in turn; the playbook prints the new expiry,
   leaves the CA alone and sets the sentinel, so
   [Kured](nodes.md#rebooting-a-node) restarts the static pods one node at a
   time.

    ```bash
    make kube-certs
    ```

2. After Kured has rebooted the last control plane, refresh the admin
   kubeconfig: `admin.conf` was renewed too, and `output/kubeconfig` carries
   the old client certificate.

    ```bash
    make kubeconfig
    ```
