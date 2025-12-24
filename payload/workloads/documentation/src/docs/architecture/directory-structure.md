# Directory Structure

```text
.
├── ansible
│   ├── inventory.yml       # Host definitions (MAC addresses, IPs, Roles)
│   ├── playbooks
│   │   ├── config.yml      # Generate configs
│   │   ├── download.yml    # Orchestrate downloads
│   │   ├── kubeconfig.yml  # Retrieve kubeconfig from control plane
│   │   └── tasks           # Download Task definitions
│   │       ├── download_flatcar.yml
│   │       ├── download_sysext.yml
│   │       └── download_syslinux.yml
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
│   ├── apps/               # ArgoCD Applications
│   │   └── nginx-test/     # User app directory
│   │       ├── application.yaml
│   │       ├── deployment.yaml
│   │       ├── httproute.yaml
│   │       ├── namespace.yaml
│   │       └── service.yaml
│   ├── argocd/             # ArgoCD config (managed by ArgoCD after bootstrap)
│   │   ├── application.yaml     # ArgoCD self-management Application
│   │   ├── argocd-values.yaml
│   │   └── argocd-httproute.yaml
│   └── core/               # Infrastructure managed by ArgoCD
│       ├── cert-manager/   # TLS certificates
│       ├── cilium/         # CNI + Gateway API
│       ├── gateway-api/    # Gateway resources
│       ├── monitoring/     # Prometheus stack
│       └── rook-ceph/      # Storage operator & cluster
└── README.md
```
