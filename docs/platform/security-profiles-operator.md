---
description: "The Kubernetes Security Profiles Operator, installed to manage SELinux policy modules as SelinuxProfile objects; a draft that cannot install a profile on Flatcar yet."
---

# Security Profiles Operator

The [Security Profiles Operator](https://github.com/kubernetes-sigs/security-profiles-operator)
(SPO) turns a `SelinuxProfile` object into a policy module on every node: a
`spod` DaemonSet runs `selinuxd` beside the host's policy store, and a pod then
uses `<profile>.process` as its `seLinuxOptions.type`. It is here to carry the
workload-specific modules that moving the nodes from permissive to enforcing
needs; the denials those modules answer are in
[Node hardening](../architecture/security.md#node-hardening). Seccomp,
AppArmor and the log enricher are off.

## At a glance

| | |
| --- | --- |
| Namespace | `security-profiles-operator` |
| Depends on | [cert-manager](cert-manager.md): the operator creates a self-signed Issuer and the webhook certificate itself |
| If it is down | No profile is installed or changed; modules already loaded stay loaded |
| Health check | `kubectl -n security-profiles-operator get spod,pods` |
| Files | `payload/platform/security-profiles-operator/application.yaml`, `payload/platform/security/network-policies/security-profiles-operator.yaml` |

## Configuration

| Setting | Why |
| --- | --- |
| `selinux.enable` | SELinux support is off outside OpenShift; the chart renders it onto the `spod` object |
| `customTemplatesConfigMap` left unset | The bundled templates do not match Flatcar's base policy and a `.cil` set for it does not exist yet; a ConfigMap that is named but absent keeps the daemon pod from starting |
| Enricher, AppArmor, BPF recorder left off | Recording needs `auditd` and a log file the nodes do not have; the denials are in Loki |
| `replicaCount` | The operator is a stateless Deployment with a PodDisruptionBudget; two replicas keep a drain from stopping reconciliation |
| `resources` | Chart defaults, never measured here; re-measure against the [2.5x rule](index.md#components) once `SelinuxProfile` objects exist, since the manager caches every profile and pod |
| `enforce: privileged` on the namespace | `selinuxd` is privileged and mounts the host's SELinux directories; see [Pod Security](security-policies.md#pod-security-admission) |
| Network policy | The operator, daemon and webhook only talk to the API server; metrics are off, so there is no scrape rule |

The images are the chart's: the operator from `registry.k8s.io` and `selinuxd`
from `quay.io`, pinned by digest inside the chart, so Renovate sees only the
chart version. The operator picks the `selinuxd` image by the node's OS; Flatcar
has no entry and gets the default.

## Usage

1. Apply a `SelinuxProfile` in any namespace.
2. Read its status: `kubectl get selinuxprofile <name> -o yaml`. `Installed` means every node's `selinuxd` loaded it.
3. Set `seLinuxOptions.type` on the pod to the profile's usage string, shown in the status.

Which denials a profile must cover, and when `spc_t` or a host-path label
needs no module at all, is the `selinux-denials` skill.

## Health check

| Check | Healthy |
| --- | --- |
| `kubectl -n security-profiles-operator get spod spod` | `STATE` is `RUNNING` |
| `kubectl -n security-profiles-operator get ds spod` | one pod per node, including the control planes |
| `kubectl get selinuxprofile -A` | every profile `Installed` |

## Pitfalls

Nothing here was run on the cluster. Known gaps:

- **The policy store is read-only.** On Flatcar `/var/lib/selinux` is a symlink
  to `/usr/lib/selinux/policy`, so `selinuxd` cannot install a module until the
  node template provides a writable copy and re-copies it after each OS update.
  That is separate work; until then no `SelinuxProfile` reaches `Installed`.
- **No templates for Flatcar's policy.** `customTemplatesConfigMap` is the
  documented answer, and the `.cil` files are still to be written.
- **Upstream does not test it.** Its Flatcar end-to-end job runs with
  `E2E_TEST_SELINUX=false`.
- **A fresh node has no profiles** until its `spod` pod runs; whatever a node
  needs to boot belongs in the node template, not in a profile.
