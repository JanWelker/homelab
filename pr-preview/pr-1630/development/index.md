# Development

How to change this repository without surprising the cluster. Manifests live under `payload/` and are applied by ArgoCD; documentation lives under `docs/`; workloads live in [`homelab-apps`](<https://github.com/JanWelker/homelab-apps>).

- **[Contributing](<https://homelab.wlkr.ch/development/contributing/index.md>)**

  ---

  Repository setup, the checks to run, commit conventions, and how the documentation site is built and published.
- **[Adding a Workload](<https://homelab.wlkr.ch/development/add-workload/index.md>)**

  ---

  Deploying a new application, from the ArgoCD Application through to the documentation page the build fails without.
- **[Adding a Platform Component](<https://homelab.wlkr.ch/development/add-platform-component/index.md>)**

  ---

  A new directory under `payload/platform/`, and everything a component needs before its first sync: the Application, the policies, the pin and the page.
- **[Maintenance](<https://homelab.wlkr.ch/development/maintenance/index.md>)**

  ---

  The CI workflows, and what Renovate is and is not allowed to merge on its own.
