# Core Concepts

Five ideas carry this cluster besides Kubernetes itself. Read this before the [Quickstart](<https://homelab.wlkr.ch/quickstart/index.md>) if any of the names below are new; the [Terms](<#terms>) table defines the words the other pages assume.

| Concept | What it is | What it costs you |
| --- | --- | --- |
| [Flatcar Container Linux](<#flatcar-container-linux>) | An immutable OS with a read-only `/usr` and A/B partition updates | No `apt install`. Anything unusual has to arrive another way |
| [Ignition & Butane](<#ignition-and-butane>) | First-boot provisioning from a JSON config | It runs *once*. Changing a template means rebuilding the node |
| [Systemd sysexts](<#systemd-sysexts>) | Read-only images that extend `/usr` at boot | Upgrading Kubernetes means swapping an image and rebooting |
| [PXE boot](<#pxe-boot>) | Booting a machine off the network instead of its disk | Needs a DHCP server you control and a host on the same segment |
| [GitOps](<#gitops>) | Git is the desired state; a controller reconciles to it | If it is not committed, it does not exist |

## Flatcar Container Linux

[Flatcar](<https://www.flatcar.org/>) is an immutable, minimal Linux distribution designed for running containers. The root filesystem is read-only — you cannot install packages or modify system files at runtime — which forces all configuration to happen declaratively, at first boot, through Ignition. A read-only `/usr` makes undocumented drift between "identical" nodes impossible rather than merely discouraged.

Updates are downloaded in the background onto the passive half of an A/B partition pair, so a bad one can be rolled back and nothing changes until the machine restarts. [Kured](<https://homelab.wlkr.ch/platform/kured/index.md>), not Flatcar's `locksmithd`, drains and reboots one node at a time — see [Updates & Upgrades](<https://homelab.wlkr.ch/operations/upgrades/index.md>).

## Ignition and Butane

**Ignition** is Flatcar's first-boot provisioning system. It reads a JSON config and applies it: creates users, writes files, partitions disks, enables systemd units.

**Butane** is the human-writable YAML that compiles to that JSON. Ansible renders Butane from Jinja2 templates on the deployment host and transpiles it; the boot server serves the result.

> [!WARNING]
>
> **Ignition runs once, in the initramfs, before the real root is mounted**
>
> Change a template and the node has to be *reprovisioned* to care — which here means arming it with `make reinstall` and network-booting it, wiping the disk on the way in. The config is embedded into the OEM partition at install time, so an installed node reads it from its own disk rather than from the network.

## Systemd sysexts

Because `/usr` is read-only, software the base image does not ship — only `kubernetes`, in this cluster — arrives as a **system extension**: a read-only squashfs image overlaid onto `/usr` at boot, managed by `systemd-sysupdate`. Nodes fetch it from the HTTP boot server on their first boot from disk. containerd is part of the base image and is deliberately not replaced: a sysext-bakery build would run a containerd Flatcar has not tested.

Upgrading Kubernetes on these nodes is therefore not `apt upgrade`; it is swapping an image and rebooting. See [Updates & Upgrades](<https://homelab.wlkr.ch/operations/upgrades/index.md>).

## PXE boot

**PXE** (Preboot Execution Environment) lets a machine boot from the network instead of a local disk. The NIC asks DHCP where to go, fetches a bootloader over TFTP, and the bootloader fetches everything else. In this project:

1. An external DHCP server hands out the boot server's IP and a syslinux filename.
2. TFTP serves the syslinux bootloader and a per-MAC boot menu.
3. Syslinux fetches the Flatcar kernel and initrd over HTTP, passing the Ignition config URL as a kernel parameter.

The full sequence is in [Boot & Bootstrap Process](<https://homelab.wlkr.ch/architecture/boot-process/index.md>).

## GitOps

Everything the cluster runs is described in this repository under `payload/`, and [ArgoCD](<https://argo-cd.readthedocs.io/>) continuously reconciles the cluster to match; a change made by hand is put back on the next sync.

## Terms

| Term | Meaning |
| --- | --- |
| [ApplicationSet](<https://homelab.wlkr.ch/architecture/gitops/index.md#structure>) | An ArgoCD object that generates an `Application` per matching file, so a component is a directory |
| [CNI](<https://homelab.wlkr.ch/platform/cilium/index.md>) | The plugin that gives pods their network; Cilium here |
| [CRD](<https://homelab.wlkr.ch/architecture/gitops/index.md#bootstrap-convergence>) | A custom resource type a component adds to the Kubernetes API |
| [eBGP](<https://homelab.wlkr.ch/platform/cilium/index.md#configuration>) | The routing protocol the control planes announce the Gateway addresses to the router with |
| [ExternalSecret](<https://homelab.wlkr.ch/platform/external-secrets/index.md>) | A resource that copies an OpenBao value into a Kubernetes `Secret` |
| [Helm chart](<https://helm.sh/docs/>) | A packaged, templated set of manifests, rendered by ArgoCD |
| [OSD](<https://homelab.wlkr.ch/platform/rook-ceph/index.md#disks>) | The Ceph daemon that owns one disk partition |
| [Seal, unseal](<https://homelab.wlkr.ch/platform/openbao/index.md#unsealing-after-a-restart>) | OpenBao starts locked and opens only with a threshold of key shares |
| [Sync policy](<https://homelab.wlkr.ch/architecture/gitops/index.md#sync-policy>) | Every `Application` syncs automatically, reverts drift and retries |
| [Sync wave](<https://homelab.wlkr.ch/architecture/gitops/index.md#workloads-sync-alongside-the-platform>) | An annotation that orders resources within one Application |
| [VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>) | The floating address the API server answers on, whichever control plane holds it |
