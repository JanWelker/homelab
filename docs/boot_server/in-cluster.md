---
description: "Running the PXE boot server inside the cluster, so a node can be rebuilt without a laptop on the rack's network segment."
---

# In-Cluster Boot Server

The same image `make serve` runs on the external boot host also runs in the
cluster, as a Deployment in `payload/platform/boot-server/`. It exists for one
situation: a node has to be rebuilt, the cluster is up, and you are not in the
room.

That was a [known limitation](../architecture/limitations.md#provisioning-requires-the-boot-server-on-the-same-segment)
until now — reprovisioning meant a machine on the nodes' L2 segment running
`make serve`, and the only such machine was the external boot host. The cluster
is also on that segment, and it already runs containers for a living.

It does not replace the external host. Nothing in the cluster can serve the
first boot of the cluster it runs in, and nothing in it can serve a rebuild
while the cluster is down. Both boot servers are the same image, the same
script, and the same `output/` directory; only the place they run differs.

## It ships scaled to zero

`replicas: 0`, on purpose. While a boot server serves, anything on the segment
can fetch the Ignition configs, and those carry the kubeadm bootstrap token and
the certificate key — between them enough to join a control-plane node. See
[Security Posture &rarr; Provisioning](../architecture/security.md#provisioning).

An external `make serve` is a foreground command in a window somebody eventually
closes. A Deployment is not: left at one replica it would quietly serve join
credentials to the house network for as long as the cluster runs. So scaling it
is the operator's decision, and `/spec/replicas` sits in the Application's
`ignoreDifferences` so that ArgoCD's `selfHeal` does not reverse it — without
that, a sync three minutes into a PXE boot scales the server away and the node
fails in the least informative way available.

## Reprovisioning a node

Four commands, in this order. The address in step 1 is the node the Deployment
is pinned to, **not** the external boot host:

```bash
make config BOOT_SERVER_IP=10.9.2.3   # regenerate PXE menus and Ignition URLs
make serve-cluster                    # scale to 1 and wait for it
make serve-cluster-push               # push output/ onto its volume
# point DHCP option 66 at 10.9.2.3, then power-cycle the node
make serve-cluster-stop               # scale back to 0 once it has joined
```

`make config BOOT_SERVER_IP=...` is the step that is easy to skip and impossible
to get away with. `boot_server_ip` is baked into the generated PXE menu as the
kernel, initrd and Ignition URLs, and into the Ignition config as the sysext
URLs; artifacts generated for the external host send the node to an address
nothing is listening on. The override applies to one run and leaves
`inventory.yaml` alone.

Then watch the log the way you would watch the external one:

```bash
kubectl -n boot-server logs -f deploy/boot-server
```

The node's progress through the boot sequence is in there, one file at a time,
and the file it stops at is the answer. The
[PXE troubleshooting table](../quickstart.md#troubleshooting-pxe-boot) applies
unchanged.

## Why it is pinned to a node

```yaml
hostNetwork: true
nodeSelector:
  kubernetes.io/hostname: loki
```

Both lines are forced, and the reasoning is the same one that makes
`--network host` mandatory on the external host:
[TFTP answers from an ephemeral port](index.md#why-host-networking). No Service
can reverse-translate that, so a `LoadBalancer` IP from the Cilium pool would
take the request and drop every reply. The pod binds the node's own interfaces
instead.

Which makes the node part of the configuration. Its address is what DHCP option
66 and `boot_server_ip` have to name, so the pod cannot be free to move: a
rescheduled boot server is a boot server at a different address, and the PXE
menus that were generated an hour ago are now wrong. A control-plane node is
pinned because those are the long-lived ones here, and workers are what this is
most likely to be rebuilding.

Two consequences worth knowing before you need them:

- **The pinned node cannot be rebuilt from the cluster.** Reprovisioning `loki`
  is a job for the external boot host, or for a pin moved to another node first.
- **Moving the pin is a commit**, and a new address for DHCP and
  `boot_server_ip`.

## Where the artifacts come from

The image carries the server; a `ReadWriteOnce` Ceph volume carries what it
serves. They are not baked into the image, for three reasons: they are around
600 MB, the Ignition configs among them are per-host, and those configs hold
join credentials that have no business in a layer in a public registry.

They are also not generated in the cluster. `make config` needs the inventory,
Butane, and the credentials under `output/credentials/`, which is how the
bootstrap token survives between runs — so generation stays on the deployment
host and `make serve-cluster-push` ships the bytes:

```bash
tar cf - -C output http tftp | kubectl exec -i deploy/boot-server -- tar xf - -C /output
```

The volume keeps them across scale-downs, so a second rebuild of the same node
needs only `make serve-cluster`. Re-run `make config` and push again after any
inventory change, a Renovate bump to `flatcar_version`, or anything else that
changes what `output/` holds.

## The image is the cluster's own

```yaml
# renovate: datasource=docker depName=ghcr.io/janwelker/homelab/boot-server
image: ghcr.io/janwelker/homelab/boot-server:latest
```

Built from `boot_server/Dockerfile` by the `build-boot-server.yaml` workflow —
see [Boot Server &rarr; The image](index.md#the-image). Two things follow from
the cluster pulling an image this repository publishes:

- **The GHCR package has to be readable by the nodes.** A new package is
  private, and a private one needs an `imagePullSecret` that does not exist
  here; the pull then fails with `denied`, which reads like a missing image
  rather than a permission. Make the package public once.
- **`make serve` reads this line.** The `Makefile` derives `BOOT_SERVER_IMAGE`
  from this manifest rather than pinning its own copy, the same way the
  bootstrap targets read their chart versions, so the external boot host and the
  cluster cannot end up serving two different builds.

## What is not here

- **No HTTPRoute, no Gateway, no certificate.** PXE firmware speaks plain HTTP
  on port 8000 and nothing else; there is nothing to publish.
- **No network policy.** A host-networked pod has the node's identity, not an
  endpoint identity a `CiliumNetworkPolicy` can select — see
  [Security Policies &rarr; Scope](../platform/security-policies.md#scope). The
  exposure while it runs is the same as the external boot server's, and the
  mitigation is the same: scale it back down.
- **No `Service`.** Nothing in the cluster consumes this; the clients are
  machines that do not have an operating system yet.
