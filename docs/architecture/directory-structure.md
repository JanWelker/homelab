---
description: "Repository layout: where Ansible playbooks, the boot server, documentation, and GitOps manifests live."
---

# Directory Structure

Four directories do the real work, and knowing which is which saves a lot of
grepping. `ansible/` describes the machines, `boot_server/` hands them their
operating system, `payload/` is everything the cluster runs, and `output/` is
generated — never edit anything in there, it will be overwritten by the next
`make config` without ceremony.

```text
.
├── ansible
│   ├── inventory.yaml       # Host definitions (MAC addresses, IPs, Roles)
│   ├── playbooks
│   │   ├── config.yaml      # Generate configs
│   │   ├── download.yaml    # Orchestrate downloads
│   │   ├── kubeconfig.yaml  # Retrieve kubeconfig from control plane
│   │   └── tasks           # Download Task definitions
│   │       ├── download_flatcar.yaml
│   │       ├── download_sysext.yaml
│   │       └── download_syslinux.yaml
│   └── templates
│       ├── butane_config.yaml.j2 # Butane config template (transpiles to Ignition)
│       ├── kubeadm.yaml.j2
│       └── pxe_config.j2         # PXE boot menu config
├── boot_server
│   └── serve.py            # Python script for HTTP & TFTP
├── docs                    # Documentation sources (this site)
│   ├── architecture/
│   ├── development/
│   ├── platform/
│   └── assets/             # Images and other static files
├── output                  # Generated files & Artifacts
│   ├── credentials/        # Bootstrap token + certificate key, plaintext
│   ├── http/               # Ignition, Flatcar artifacts, Sysext images
│   ├── kubeconfig          # Admin Kubeconfig file
│   ├── tftp/               # PXE bootloader & configs
│   └── tmp/                # Temporary workspace
├── payload                 # K8s Manifests & Bootstrap scripts
│   ├── root.yaml           # Parent Applications (platform, gitops)
│   ├── argocd/             # ArgoCD config (managed by ArgoCD after bootstrap)
│   │   ├── application.yaml     # ArgoCD self-management Application
│   │   ├── argocd-projects.yaml # AppProject grouping (see Security Posture)
│   │   ├── httproute.yaml
│   │   └── values.yaml
│   └── platform/           # Core infrastructure managed by ArgoCD
│       ├── cert-manager/     # TLS certificates
│       ├── cilium/           # CNI + Gateway API
│       ├── external-secrets/ # OpenBao to K8s Secret bridge
│       ├── gateway-api/      # Gateway resources
│       ├── monitoring/       # Prometheus stack
│       ├── openbao/          # Cluster secret store
│       └── rook-ceph/        # Storage operator & cluster
├── zensical.toml           # Documentation site configuration
└── README.md
```

A useful mental split: `ansible/` and `boot_server/` only matter while a node is
being built. `payload/` matters every day after that. If you are debugging a
running cluster and find yourself in `ansible/`, you are probably in the wrong
place — with the honourable exception of `kubeadm.yaml.j2`, which explains why
half the control plane is configured the way it is.
