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
| Files | `payload/platform/security-profiles-operator/`, `payload/platform/security/network-policies/security-profiles-operator.yaml` |

## Configuration

| Setting | Why |
| --- | --- |
| `selinuxd-memory-request` in `admission-policies.yaml` | With `selinux.enable`, each `spod` pod gains a `selinuxd` container with a fixed 512Mi request at node-critical priority, preempting pods on a full node; the policy halves it at pod creation |
| `customTemplatesConfigMap` | The bundled templates name attributes Flatcar's policy lacks, so nothing that inherits `container` would load; see [Templates](#templates) |
| `selinuxdImage.default` | The chart's `el9` image. Flatcar matches none of the operator's OS patterns and gets the default, whose RHEL 8 tooling fails on Flatcar's base policy; of the four images only `el9` builds it. The digest is a copy of the chart's |
| Enricher, AppArmor, BPF recorder left off | Recording needs `auditd` and a log file the nodes do not have; the denials are in Loki |
| `replicaCount` | The operator is a stateless Deployment with a PodDisruptionBudget; two replicas keep a drain from stopping reconciliation |
| `resources` | Chart defaults, never measured here; re-measure against the [2.5x rule](index.md#components) once `SelinuxProfile` objects exist, since the manager caches every profile and pod |
| `enforce: privileged` on the namespace | `selinuxd` is privileged and mounts the host's SELinux directories; see [Pod Security](security-policies.md#pod-security-admission) |
| Network policy | The operator, daemon and webhook only talk to the API server; metrics are off, so there is no scrape rule |

The images are the chart's: the operator from `registry.k8s.io` and `selinuxd`
from `quay.io`, pinned by digest inside the chart, so Renovate sees only the
chart version.

### Policy store

Flatcar's `/var/lib/selinux` is a symlink into the read-only `/usr`, where
`selinuxd` cannot install anything. `selinux-policy-store.service` in
`butane_node_config.yaml.j2` replaces it with a copy before the kubelet
starts, and copies again when the Flatcar version changes, dropping the policy
`selinuxd` compiled from the old base; the daemon installs its modules again
when its pod starts. Until it has, a pod whose type comes from a profile
cannot start on that node.

### Templates

`selinuxd-templates.yaml` replaces the image's template set, which does not
load on Flatcar: it names two Fedora attributes Flatcar lacks and a `proc_type`
rule Flatcar's `neverallow` rules forbid. `base_container.cil` is generated
from a node's own `container` module by the skill's `gen_base_template.py`,
because Flatcar grants most of `container_t`'s rights to the type itself: a
profile's type starts with all of them and adds its own.
`flatcar_runtime.cil` lets the container runtime, which runs as `kernel_t`
here, start an `spc_t` container that sets `no_new_privs`. The daemon reads
the files once, so a change takes a new ConfigMap name. To test one, run the
`el9` image's `semodule -n -i` against a copy of a node's `/var/lib/selinux`
and `/etc/selinux`: `-n` builds without loading.

## Usage

A profile is one entry in `profiles.yaml`, written from the denials Loki holds
by the `selinux-denials` skill, which also says when `spc_t` or a host-path
label needs no profile.

1. Merge the profile and wait for `kubectl get selinuxprofile <name>` to show `Installed`.
2. Then set `seLinuxOptions.type: <name>.process` on the container, in a second merge: a pod whose type a node does not know cannot start.

## Health check

| Check | Healthy |
| --- | --- |
| `kubectl -n security-profiles-operator get spod spod` | `STATE` is `RUNNING` |
| `kubectl -n security-profiles-operator get ds spod` | one pod per node, including the control planes |
| `kubectl get selinuxprofile -A` | every profile `Installed` |

## Pitfalls

Known gaps:

- **Two policy files.** `selinuxd` writes `policy.33`, the kernel's version,
  beside the shipped `policy.35`; a `semodule -B` with the host's tools
  rewrites only the latter, and the node then boots the stale one.
- **Upstream does not test it.** Its Flatcar end-to-end job runs with
  `E2E_TEST_SELINUX=false`.
- **A fresh node has no profiles** until its `spod` pod runs; whatever a node
  needs to boot belongs in the node template.
