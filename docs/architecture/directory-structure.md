---
description: "Repository layout: where Ansible playbooks, the boot server, documentation, and GitOps manifests live."
---

# Directory Structure

Four directories do the real work, and knowing which is which saves a lot of
grepping. `ansible/` describes the machines, `boot_server/` hands them their
operating system, `payload/` is the platform the cluster runs on, and `output/`
is generated — never edit anything in there, it will be overwritten by the next
`make config` without ceremony.

The applications the cluster exists to *serve* are not here at all: they live
in [`homelab-apps`](https://github.com/JanWelker/homelab-apps), and
[GitOps Strategy](gitops.md#workloads-live-in-a-second-repository) explains why.

```text
.
├── ansible
│   ├── inventory.yaml       # Host definitions (MAC addresses, IPs, Roles)
│   ├── playbooks
│   │   ├── config.yaml      # Generate configs
│   │   ├── download.yaml    # Orchestrate downloads
│   │   ├── kubeconfig.yaml  # Retrieve kubeconfig from control plane
│   │   ├── reinstall.yaml   # Arm or disarm the generated PXE menus
│   │   └── tasks           # Download Task definitions
│   │       ├── download_flatcar.yaml
│   │       ├── download_sysext.yaml
│   │       └── download_syslinux.yaml
│   └── templates
│       ├── butane_installer_config.yaml.j2 # PXE environment: wipe, install, reboot
│       ├── butane_node_config.yaml.j2      # Installed system: partitions, files, units
│       ├── kubeadm.yaml.j2
│       └── pxe_config.j2                   # PXE boot menu config
├── boot_server
│   └── serve.py            # Python script for HTTP & TFTP
├── docs                    # Documentation sources (this site)
│   ├── architecture/
│   ├── operations/
│   ├── platform/
│   ├── development/
│   └── assets/             # Images, fonts and other static files
├── output                  # Generated files & Artifacts -- see the warning below
│   ├── credentials/        # Bootstrap token, certificate key, etcd
│   │                       # encryption key -- plaintext, 0700
│   ├── http/               # Ignition, Flatcar artifacts, Sysext images
│   ├── kubeconfig          # Admin Kubeconfig file
│   ├── tftp/               # PXE bootloader & configs
│   └── tmp/                # Temporary workspace
├── payload                 # K8s Manifests & Bootstrap scripts
│   ├── argocd/             # ArgoCD itself (managed by ArgoCD after bootstrap)
│   │   ├── application.yaml     # Self-management Application, applied at bootstrap
│   │   ├── applicationset-platform.yaml  # One Application per platform/*/application.yaml
│   │   └── values.yaml
│   └── platform/           # Core infrastructure managed by ArgoCD
│       ├── argocd-config/    # ArgoCD's HTTPRoute, OIDC secret, dashboard
│       ├── argocd-projects/  # AppProjects (see Security Posture)
│       ├── cert-manager/     # cert-manager controller
│       ├── certificates/     # Let's Encrypt issuers and TLS certificates
│       ├── cilium/           # CNI + Gateway API
│       ├── external-secrets/ # OpenBao to K8s Secret bridge
│       ├── gateway-api/      # Gateway resources
│       ├── monitoring/       # Prometheus stack
│       ├── openbao/          # Cluster secret store
│       ├── rook-ceph/        # Storage operator & cluster
│       ├── workloads/         # The `apps` ApplicationSet, pointed at homelab-apps
│       └── ...               # One directory per component; see Platform
├── zensical.toml           # Documentation site configuration
└── README.md
```

!!! danger "`output/` is generated; `output/credentials/` is not regenerable"
    Everything under `output/credentials/` is generated *once* and read back on every later run: the kubeadm token, the certificate key, the etcd encryption key that decrypts every Secret in the cluster, and the five OpenBao unseal keys. `make config` writes **new** values for any that are missing, which a cluster already running on the old ones will not accept. Losing all five unseal keys loses every secret the cluster holds, permanently.

`make clean` empties the whole of `output/`, so it prints that inventory and
asks before it does anything; it refuses outright when it is not attached to a
terminal. `make clean-artifacts` removes only the regenerable half — `http/`,
`tftp/` and `tmp/` — and is the one to reach for when you just want a fresh
download.

| Target | Removes | Keeps |
| --- | --- | --- |
| `make clean-artifacts` | `output/http`, `output/tftp`, `output/tmp` | `output/credentials/`, `output/kubeconfig` |
| `make clean` | everything under `output/` | nothing — it asks first |

A useful mental split: `ansible/` and `boot_server/` only matter while a node is
being built. `payload/` matters every day after that. If you are debugging a
running cluster and find yourself in `ansible/`, you are probably in the wrong
place — with the honourable exception of `kubeadm.yaml.j2`, which explains why
half the control plane is configured the way it is.

## The workloads repository

`homelab-apps` is one directory per application, its own documentation, and
very little else:

```text
.
├── docs                    # Its own site, published separately
│   ├── index.md
│   ├── conventions.md      # The rules every app directory follows
│   ├── home-assistant.md
│   └── nextcloud.md
├── zensical.toml
├── renovate.json           # Same policy as this repository
├── home-assistant/
│   ├── application.yaml    # The ArgoCD Application, read by the `apps` set
│   ├── namespace.yaml      # Pod Security labels, sync wave -2
│   ├── database.yaml       # CloudNativePG Cluster, sync wave -1
│   ├── ...                 # Whatever that application is made of
│   ├── httproute.yaml
│   └── networkpolicy.yaml
└── nextcloud/
    └── ...
```

The shape is deliberately the same as `payload/platform/`: exactly one
`application.yaml` per directory, and everything beside it is what that
Application deploys. Adding a directory adds an application; there is no list
to register it in. See [Adding a Workload](../development/add-workload.md).

Its documentation is its own site, at
[janwelker.github.io/homelab-apps](https://janwelker.github.io/homelab-apps/),
built the same way this one is. Two repositories, two sites: a change to a
workload and the page describing it belong in one pull request, and neither
site has to be rebuilt because the other changed.
