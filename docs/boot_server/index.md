---
description: "The containerised Python PXE boot server that serves TFTP and HTTP artifacts to bare metal nodes during provisioning."
---

# Boot Server

A single Python script that runs a TFTP server and an HTTP server, which is all
a bare metal node needs to talk itself into having an operating system.

There is a whole industry of PXE appliances, netboot frameworks and provisioning
platforms, and every one of them is eventually doing this: answer a TFTP request
with a bootloader, then answer HTTP requests with a kernel, an initrd, and a
config. For six machines on one switch, `serve.py` is the right amount of
software.

It runs as a container. The script itself is unchanged by that — it reads its
address, its ports and its artifact directory from the environment, so the same
image serves from the deployment host during a first build and, once there is a
cluster, [from inside it](in-cluster.md) when a node has to be rebuilt. This page
is the external one; the first boot of a cluster is always served from outside
it.

## Usage

```bash
make serve
```

That starts the published image with the host's network namespace, the repo's
`output/` bind-mounted read-only at `/output`, and `BIND_IP` set to
`boot_server_ip` from `ansible/inventory.yaml` — the same value the generated PXE
menus tell nodes to fetch from. No `sudo`: the container gets
`NET_BIND_SERVICE` for port 69 and nothing else.

Leave it in the foreground where you can see it. The request log is the best
diagnostic tool in the whole provisioning process — you can watch a node
progress through the boot sequence one file at a time, and the file it *stops*
at tells you exactly what is wrong. See
[Troubleshooting PXE boot](../quickstart.md#troubleshooting-pxe-boot).

!!! warning "Stop it when you are done"
    While it is running, anything on the network segment can fetch the Ignition configs, which embed the kubeadm bootstrap token and certificate key — together enough to join a control-plane node. The token expires in 24 hours and the certificate key in two, so the real mitigation is simply not leaving this running for a month. `Ctrl-C` is enough; the container handles `SIGTERM` and exits rather than waiting out a kill timer. See [Security Posture](../architecture/security.md#provisioning).

### Configuration

Everything is an environment variable, with defaults that suit a container:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BIND_IP` | `0.0.0.0` | Address both servers bind. `make serve` passes the inventory's `boot_server_ip` |
| `OUTPUT_DIR` | `/output` in the image | Parent of the two served directories |
| `HTTP_ROOT` | `$OUTPUT_DIR/http` | Overrides the HTTP root outright |
| `TFTP_ROOT` | `$OUTPUT_DIR/tftp` | Overrides the TFTP root outright |
| `HTTP_PORT` | `8000` | — |
| `TFTP_PORT` | `69` | Privileged, hence `NET_BIND_SERVICE` |

### Why host networking

`--network host` is load-bearing, and this is the part that costs an evening if
you try to remove it. TFTP sends its first reply from a *fresh ephemeral port*,
not from port 69 — the log says so on every transfer:

```text
[INFO] Setting tidport to 58293
```

A published port or a NAT'd bridge only reverses the tuple it mapped. The data
packets go out from a port nothing has a translation for, the node sees a reply
from an address it never spoke to, and PXE firmware drops it in silence. The
symptom is a node that fetches its bootloader and then hangs, which sends you
looking for a corrupt image rather than a NAT table.

Two consequences:

- **Docker Desktop on macOS cannot host this.** Its "host" network is the
  Linux VM's, not the Mac's, so nothing on the LAN can reach port 69 at all.
  Run the container on the Linux machine that sits on the nodes' segment.
- **Rootless Podman needs help with port 69.** Either run it with `sudo`, or
  set `net.ipv4.ip_unprivileged_port_start=69` on the host.

## The image

`boot_server/Dockerfile` installs `tftpy` on top of `python:3.14-slim` and
copies in the script. The `build-boot-server.yaml` workflow builds it on every
push to `main` that touches `boot_server/**` and pushes it to
`ghcr.io/janwelker/homelab/boot-server`, tagged `latest` and with the full
commit SHA. Pull requests build it and throw the result away — see
[Maintenance](../development/maintenance.md#container-images).

It is built for `linux/amd64` only. The hosts that can usefully serve PXE here
are x86, and an arm64 image would only ever run where host networking does not
reach the LAN.

!!! tip "The package starts private"
    A newly created GHCR package is private, and `docker pull` then fails with `denied` rather than anything about authentication. Either make it public once — *Packages &rarr; boot-server &rarr; Package settings &rarr; Change visibility* — or `docker login ghcr.io` with a PAT carrying `read:packages`.

To serve a change to `serve.py` that CI has not published yet, build from the
working tree instead:

```bash
make serve-dev
```

## Components

The `serve.py` script implements two threaded servers:

1. **TFTP Server (Port 69)**:
    - Serves the bootloader: `lpxelinux.0` (BIOS) or `syslinux.efi` (UEFI).
    - Serves PXE configuration files from `pxelinux.cfg/`.

2. **HTTP Server (Port 8000)**:
    - Serves large artifacts that are too slow/unreliable over TFTP.
    - **Kernel & Initrd**: Flatcar Linux boot files.
    - **Ignition Configs**: Generated JSON configurations.
    - **Sysext Images**: Custom system extensions (Kubernetes, Containerd).

The split is not arbitrary. TFTP has no windowing and no real error recovery —
it is fine for a bootloader measured in kilobytes and miserable for a kernel.
Everything moves to HTTP the moment there is something capable of speaking it.

The HTTP side is threaded, which matters once more than one node boots at a
time: six machines pulling the same 374 MB initrd from a single-threaded server
are served one after another, and the ones at the back of the queue time out in
the firmware, where nothing logs anything.

## Directory Serving

The server shares the `output/` directory of the project root, mounted
read-only.

- TFTP Root: `output/tftp`
- HTTP Root: `output/http`

Both are generated by `make config` and `make download`. If a node requests a
file that is not there, the fix is almost always `make artifacts` rather than
anything in this script.
