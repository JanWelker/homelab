# Boot Server

This component runs a combined TFTP and HTTP server to support PXE booting of
the bare metal nodes.

## Usage

The server is started via the Makefile:

```bash
make serve
```

This requires `sudo` privileges to bind to the privileged port 69 (TFTP).

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

## Directory Serving

The server shares the `output/` directory of the project root.

- TFTP Root: `output/tftp`
- HTTP Root: `output/http`
