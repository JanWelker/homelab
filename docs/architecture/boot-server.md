---
description: "The Python PXE boot server that serves TFTP and HTTP artifacts to bare metal nodes during provisioning."
---

# Boot Server

A single Python script that runs a TFTP server and an HTTP server, which is all
a bare metal node needs to talk itself into having an operating system.

There is a whole industry of PXE appliances, netboot frameworks and provisioning
platforms, and every one of them is eventually doing this: answer a TFTP request
with a bootloader, then answer HTTP requests with a kernel, an initrd, and a
config. For six machines on one switch, `serve.py` is the right amount of
software.

## Usage

The server is started via the Makefile:

```bash
make serve
```

This requires `sudo` privileges to bind to the privileged port 69 (TFTP), and it
has to be run from the repository root — `serve.py` resolves both document roots
relative to the working directory.

It binds the address it reads from `boot_server_ip` in
`ansible/inventory.yaml` — the same value `make config` baked into every kernel,
initrd and Ignition URL in the generated menus. There is one copy of that
address, so a server that starts is a server listening where the nodes are
asking.

If the machine holds no such interface it says so and names the variable rather
than failing with a bind error about an address you would then have to trace:

```console
error: no interface on this machine holds 10.9.200.222 -- that is
boot_server_ip in ansible/inventory.yaml, and the address every generated PXE
menu points at. Fix it there and re-run make config
```

Both servers bind that address and nothing else, and HTTP refuses directory
listings: the Ignition configs it serves carry the cluster's join credentials
and its etcd encryption key, so the segment the nodes are on is the whole
audience. If port 8000 is already taken the server exits rather than serving
menus over TFTP for a kernel fetch that would then fail.

Leave it in the foreground where you can see it. The request log is the best
diagnostic tool in the whole provisioning process — you can watch a node
progress through the boot sequence one file at a time, and the file it *stops*
at tells you exactly what is wrong. See
[Troubleshooting PXE boot](../quickstart.md#troubleshooting-pxe-boot).

It opens by saying what it is serving and which nodes are armed, then narrates
one line per request, against the node that made it. If any node is armed, that
opening is a boxed warning naming every disk about to be wiped, with the two
cancel commands underneath it — `make reinstall-cancel`, and the `LIMIT=<node>`
form for one. If no menus exist at all it says so and names `make config`, which
is the first thing a new user hits.

```console
20:33:04  server        http on 10.9.200.222:8000 from output/http
20:33:04  server        tftp on 10.9.200.222:69 from output/tftp
20:33:04  server        armed to install: odin, thor
20:33:04  server        booting from disk: freya, heimdall, loki, valkyrie
20:34:17  odin          collecting the bootloader (lpxelinux.0)
20:34:17  odin          collecting its boot menu -- armed, so it will install
20:34:19  odin          collecting the kernel
20:34:21  odin          collecting the initrd (391.2 MB)
20:34:48  odin          collecting its Ignition config
20:34:48  odin          collecting the OS image (1.2 GB) -- this is the long one
20:36:12  odin          OS image delivered -- switching to local boot, so the reboot lands on the disk
20:38:40  odin          collecting the kubernetes sysext (61.4 MB)
```

Two things make that possible, and both are worth knowing about when a line
comes out wrong. The node is identified by MAC from the `01-<mac>` menu it
fetches over TFTP, which also pins its DHCP address for the rest of the boot;
and after the install it is identified again by name, from the
`ignition-<host>.json` it asks for. A request from an address that has done
neither is logged against the bare IP rather than guessed at.

## Switching back to local boot

`make reinstall` arms a node by writing `DEFAULT install` into its menu, and
nothing in the generated files ever writes that back. On firmware that network
boots before it tries the disk, the reboot at the end of an install therefore
reads the same armed menu and starts the installer again — a node that
reinstalls in a loop, forever, with no error anywhere.

So the boot server disarms it. When a node has taken delivery of the whole OS
image, the installer has what it needs and the next thing it will do is write
the disk and reboot; that is the moment the menu is rewritten to
`DEFAULT localboot`. It is the same one-line edit `make reinstall-cancel`
makes, and re-running `make config` regenerates the file from the template
either way.

A node that never fetched an Ignition config cannot be identified by name, so
the server cannot disarm it either; it says so and tells you to run
`make reinstall-cancel` yourself.

### On the way out

Arming survives the boot server. The menu is a file on disk, so a node left
armed installs the next time it powers on whether or not anything is serving.

`Ctrl-C` therefore checks. If anything is still armed it prints what, explains
that the menu does not need the boot server to fire, and offers to disarm:

```console
  Disarm 2 node(s) now? [Y/n]
```

Enter accepts. Anything else leaves them armed and repeats the cancel command.
When stdin is not a terminal — a CI run, a `nohup` — it cannot ask, so it warns
and leaves them armed.

!!! warning "It disarms on delivery, not on success"
    The boot server sees an HTTP transfer complete; it cannot see whether `flatcar-install` then wrote the disk. An install that fails *after* the download leaves a node that is disarmed and has no working disk. That is loud rather than subtle — `flatcar-install.service` deliberately does not reboot on failure, so the node sits in the PXE environment with its journal — but the fix is `make reinstall LIMIT=<node>` before you power cycle it, not just a reboot.

!!! warning "Stop it when you are done"
    While it is running, anything on the network segment can fetch the Ignition configs, which embed the kubeadm bootstrap token and certificate key — together enough to join a control-plane node. The token expires in 24 hours and the certificate key in two, so the real mitigation is simply not leaving this running for a month. See [Security Posture](../architecture/security.md#provisioning).

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

## Directory Serving

The server shares the `output/` directory of the project root.

- TFTP Root: `output/tftp`
- HTTP Root: `output/http`

Both are generated by `make config` and `make download`. If a node requests a
file that is not there, the fix is almost always `make artifacts` rather than
anything in this script.
