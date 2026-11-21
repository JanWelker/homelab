# Security Profiles Operator

The [Security Profiles Operator](<https://github.com/kubernetes-sigs/security-profiles-operator>) (SPO) turns a `SelinuxProfile` object into a policy module on every node: a `spod` DaemonSet runs `selinuxd` beside the host's policy store, and a pod then uses `<profile>.process` as its `seLinuxOptions.type`. It is here to carry the workload-specific modules that moving the nodes from permissive to enforcing needs; the denials those modules answer are in [Node hardening](<https://homelab.wlkr.ch/architecture/security/index.md#node-hardening>). Seccomp, AppArmor and the log enricher are off.

## At a glance

|  |  |
| --- | --- |
| Namespace | `security-profiles-operator` |
| Depends on | [cert-manager](<https://homelab.wlkr.ch/platform/cert-manager/index.md>): the operator creates a self-signed Issuer and the webhook certificate itself |
| If it is down | No profile is installed or changed; modules already loaded stay loaded |
| Health check | `kubectl -n security-profiles-operator get spod,pods` |
| Files | `payload/platform/security-profiles-operator/`, `payload/platform/security/network-policies/security-profiles-operator.yaml` |

## Configuration

| Setting | Why |
| --- | --- |
| `selinuxd-memory-request` in `admission-policies.yaml` | With `selinux.enable`, each `spod` pod gains a `selinuxd` container with a fixed 512Mi request at node-critical priority, preempting pods on a full node; the policy halves it at pod creation |
| `customTemplatesConfigMap` | The bundled templates name attributes Flatcar's policy lacks, so nothing that inherits `container` would load; see [Templates](<#templates>) |
| `selinuxdImage.default` | The chart's `el9` image. Flatcar matches none of the operator's OS patterns and gets the default, whose RHEL 8 tooling fails on Flatcar's base policy; of the four images only `el9` builds it. The digest is a copy of the chart's |
| Enricher, AppArmor, BPF recorder left off | Recording needs `auditd` and a log file the nodes do not have; the denials are in Loki |
| `replicaCount` | The operator is a stateless Deployment with a PodDisruptionBudget; two replicas keep a drain from stopping reconciliation |
| `resources` | Chart defaults, never measured here; re-measure against the [2.5x rule](<https://homelab.wlkr.ch/platform/index.md#components>) once `SelinuxProfile` objects exist, since the manager caches every profile and pod |
| `enforce: privileged` on the namespace | `selinuxd` is privileged and mounts the host's SELinux directories; see [Pod Security](<https://homelab.wlkr.ch/platform/security-policies/index.md#pod-security-admission>) |
| Network policy | The operator, daemon and webhook only talk to the API server; metrics are off, so there is no scrape rule |

The images are the chart's: the operator from `registry.k8s.io` and `selinuxd` from `quay.io`, pinned by digest inside the chart, so Renovate sees only the chart version.

### Templates

`selinuxd-templates.yaml` is the image's template set with three changes: `svirt_sandbox_domain` is gone and `sandbox_net_domain` is `container_net_domain`, because Flatcar's policy has neither Fedora attribute, and the `proc_type` rule names `proc_t`, because Flatcar's `neverallow` rules forbid the attribute. To test a change, run the `el9` image's `semodule -n -i` against a copy of a node's `/var/lib/selinux` and `/etc/selinux`: `-n` builds without loading.

## Usage

1. Apply a `SelinuxProfile` in any namespace.
2. Read its status: `kubectl get selinuxprofile <name> -o yaml`. `Installed` means every node's `selinuxd` loaded it.
3. Set `seLinuxOptions.type` on the pod to the profile's usage string, shown in the status.

Which denials a profile must cover, and when `spc_t` or a host-path label needs no module at all, is the `selinux-denials` skill.

## Health check

| Check | Healthy |
| --- | --- |
| `kubectl -n security-profiles-operator get spod spod` | `STATE` is `RUNNING` |
| `kubectl -n security-profiles-operator get ds spod` | one pod per node, including the control planes |
| `kubectl get selinuxprofile -A` | every profile `Installed` |

## Pitfalls

Known gaps:

- **The policy store is read-only.** On Flatcar `/var/lib/selinux` is a symlink to `/usr/lib/selinux/policy`, so `selinuxd` cannot install a module until the node template provides a writable copy and re-copies it after each OS update. Until then no `SelinuxProfile` reaches `Installed`.
- **Two policy files.** `selinuxd` writes `policy.33`, the kernel's version, beside the shipped `policy.35`; a `semodule -B` with the host's tools rewrites only the latter, and the node then boots the stale one.
- **Upstream does not test it.** Its Flatcar end-to-end job runs with `E2E_TEST_SELINUX=false`.
- **A fresh node has no profiles** until its `spod` pod runs; whatever a node needs to boot belongs in the node template.
