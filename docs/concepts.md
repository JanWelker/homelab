---
description: "The five ideas this cluster is built on — immutable OS, first-boot provisioning, system extensions, network boot, and GitOps — and what each one costs you."
---

# Core Concepts

Five ideas carry this cluster, and none of them is Kubernetes; Kubernetes is
the ordinary part. What makes the project unusual is how the machines
underneath it come to exist and stay current.

Read this before the [Quickstart](quickstart.md) if any of the names in the
table are new. Skip it if none of them are.

| Concept | What it is | What it costs you |
| --- | --- | --- |
| [Flatcar Container Linux](#flatcar-container-linux) | An immutable OS with a read-only `/usr` and A/B partition updates | No `apt install`. Anything unusual has to arrive another way |
| [Ignition & Butane](#ignition-and-butane) | First-boot provisioning from a JSON config | It runs *once*. Changing a template means rebuilding the node |
| [Systemd sysexts](#systemd-sysexts) | Read-only images that extend `/usr` at boot | Upgrading Kubernetes means swapping an image and rebooting |
| [PXE boot](#pxe-boot) | Booting a machine off the network instead of its disk | Needs a DHCP server you control and a host on the same segment |
| [GitOps](#gitops) | Git is the desired state; a controller reconciles to it | If it is not committed, it does not exist |

## Flatcar Container Linux

[Flatcar](https://www.flatcar.org/) is an immutable, minimal Linux distribution
designed for running containers. The root filesystem is read-only — you cannot
install packages or modify system files at runtime — which forces all
configuration to happen declaratively, at first boot, through Ignition. A
read-only `/usr` makes undocumented drift between "identical" nodes impossible
rather than merely discouraged.

Updates are downloaded in the background onto the passive half of an A/B
partition pair, so a bad one can be rolled back and nothing changes until the
machine restarts. **Nothing here reboots itself**: this project masks
`locksmithd`, the daemon that would normally coordinate that, and hands the job
to [Kured](platform/kured.md), which drains the node first. See
[Updates & Upgrades](operations/upgrades.md).

## Ignition and Butane

**Ignition** is Flatcar's first-boot provisioning system. It reads a JSON config
and applies it: creates users, writes files, partitions disks, enables systemd
units.

**Butane** is the human-writable YAML that compiles to that JSON. Ansible
renders Butane from Jinja2 templates on the deployment host and transpiles it;
the boot server serves the result.

!!! warning "Ignition runs once, in the initramfs, before the real root is mounted"
    It is not a configuration management system and it converges nothing on the second boot. Change a template and the node has to be *reprovisioned* to care — which here means arming it with `make reinstall` and network-booting it, wiping the disk on the way in. The config is embedded into the OEM partition at install time, so an installed node reads it from its own disk rather than from the network.

## Systemd sysexts

Because `/usr` is read-only, software the base image does not ship — `kubernetes`
and `containerd`, in this cluster — arrives as **system extensions**: read-only
squashfs images overlaid onto `/usr` at boot, managed by `systemd-sysupdate`.
Nodes fetch them from the HTTP boot server on their first boot from disk.

Upgrading Kubernetes on these nodes is therefore not `apt upgrade`; it is
swapping an image and rebooting. See
[Updates & Upgrades](operations/upgrades.md).

## PXE boot

**PXE** (Preboot Execution Environment) lets a machine boot from the network
instead of a local disk. The NIC asks DHCP where to go, fetches a bootloader
over TFTP, and the bootloader fetches everything else. In this project:

1. An external DHCP server hands out the boot server's IP and a syslinux filename.
2. TFTP serves the syslinux bootloader and a per-MAC boot menu.
3. Syslinux fetches the Flatcar kernel and initrd over HTTP, passing the Ignition
   config URL as a kernel parameter.

TFTP runs over UDP with no error correction worth the name, which is why
everything larger than the bootloader moves to HTTP as fast as possible. The
full sequence is in [Boot & Bootstrap Process](architecture/boot-process.md).

## GitOps

Everything the cluster runs is described in this repository under `payload/`,
and [ArgoCD](https://argo-cd.readthedocs.io/) continuously reconciles the
cluster to match. If it is not in Git, it is not in the cluster — and if you
put it in the cluster anyway, the next sync of the Application that owns it
puts Git's version back.

Two terms recur throughout this site:

- **ApplicationSet** — one ArgoCD object that generates an `Application` per
  matching file in the repository, so adding a component is adding a directory.
- **Sync policy** — every `Application` syncs automatically, reverts drift
  and retries a failed sync, so nothing orders the platform: a fresh cluster
  converges as CRDs, storage and secrets appear. The order it settles in is in
  [Platform &rarr; Rollout order](platform/index.md#rollout-order); the
  policy itself is in [GitOps Strategy](architecture/gitops.md#sync-policy).
