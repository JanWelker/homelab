# Directory Structure

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
├── output                  # Generated files & Artifacts
│   ├── credentials/        # Security artifacts
│   ├── http/               # Ignition, Flatcar artifacts, Sysext images
│   ├── kubeconfig          # Admin Kubeconfig file
│   ├── tftp/               # PXE bootloader & configs
│   └── tmp/                # Temporary workspace
├── payload                 # K8s Manifests & Bootstrap scripts
│   ├── root.yaml           # Parent Applications (workloads, platform, gitops)
│   ├── argocd/             # ArgoCD config (managed by ArgoCD after bootstrap)
│   │   ├── application.yaml     # ArgoCD self-management Application
│   │   ├── argocd-projects.yaml # RBAC project definitions
│   │   ├── httproute.yaml
│   │   └── values.yaml
│   ├── platform/           # Core infrastructure managed by ArgoCD
│   │   ├── cert-manager/   # TLS certificates
│   │   ├── cilium/         # CNI + Gateway API
│   │   ├── gateway-api/    # Gateway resources
│   │   ├── infisical/      # Secrets management
│   │   ├── k8up/           # Backup operator
│   │   ├── monitoring/     # Prometheus stack
│   │   ├── postgres-operator/ # PostgreSQL HA operator
│   │   └── rook-ceph/      # Storage operator & cluster
│   └── workloads/          # User-facing applications
│       ├── advent-wollbi/
│       ├── documentation/
│       ├── github-actions-runner/
│       ├── home-assistant/
│       ├── nextcloud/
│       ├── nginx-test/
│       └── wichteln/
└── README.md
```
