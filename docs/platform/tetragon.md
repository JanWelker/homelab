---
description: "Runtime detection with Tetragon: what it records from every node, which policies it enforces, and how the events reach Loki and Alertmanager."
---

# Tetragon

[Tetragon](https://tetragon.io/) watches processes at the kernel, with eBPF,
on every node. Trivy says what an image *could* do; Tetragon records what a
process *did*: every exec and exit in a pod, and, where a `TracingPolicy`
says so, file opens and credential changes. It is the only thing here that
sees a shell spawned in a running container.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system` |
| Depends on | Kernel BTF at `/sys/kernel/btf/vmlinux`, which Flatcar ships; [Logging](logging.md) for the events, [Monitoring](monitoring.md) for the alerts |
| If it is down | No runtime record and no runtime alerts; nothing else notices |
| Health check | `kubectl get tracingpolicies -o wide`; `TetragonPolicyNotLoaded` and `TetragonEventsLost` otherwise |
| Dashboard | [Tetragon](https://monitoring.infra.k8s.wlkr.ch/d/tetragon-overview): agent health, event rates, policy hits by binary, and the latest policy hits, shells and execs read from Loki |
| Files | `payload/platform/tetragon/`: the chart, the policies, `prometheusrule.yaml` and a dashboard written for this cluster, since upstream ships none |

## Configuration

Cilium's own chart, from `helm.cilium.io`; Tetragon does not depend on Cilium
and runs privileged on the host network by the chart's default.

| Setting | Why |
| --- | --- |
| Export to stdout | The chart runs a sidecar that tails Tetragon's JSON file to stdout, so [Alloy](logging.md) collects it as any container log: `{namespace="kube-system", container="export-stdout"}` |
| `exportDenyList` | Exec and exit events from the host, `cilium` and `kube-system` stay out of the export; they are the bulk of the volume and the least interesting. Policy hits from those namespaces still pass, which is the point of the `event_set` field |
| `enableProcessCred` | Capabilities and `privileges_changed` on every exec, so a setuid binary or a file-capability escalation is visible without a policy |
| Two `ServiceMonitor`s | `tetragon_policy_events_total` is what the alert reads; `tetragon_tracingpolicy_loaded` and the ring-buffer counters are its health |
| Memory limit | 2.5x the measured working set of the busiest agent with the policy library loaded, per the [platform rule](index.md#components); the dashboard's first row shows the current worst node against it |

### Policies

`tracingpolicies.yaml` carries the cluster-scoped `TracingPolicy` objects,
adapted from the [upstream policy library](https://tetragon.io/docs/policy-library/observability/).
All post at most one event per minute per selector, and all but the first
watch containers only: the host has the node journal. Every hook carries a
`message` and one of upstream's `observability.*` tags, so an event says
what it is without a lookup here.

| Policy | Hook | Fires on | Alerted |
| --- | --- | --- | --- |
| `sensitive-file-access` | `security_file_permission` | A read under `/etc/kubernetes/pki/`, `/etc/kubernetes/enc/`, `/var/lib/etcd/` or `/var/lib/kubelet/pki/` by anything but the control-plane binaries, `kubeadm` and `kube-vip`; any read of `/etc/shadow` or the admin kubeconfigs by anything but `kube-vip` and `kubeadm`; any write, host or container, to `/etc/passwd`, `/etc/shadow`, `/etc/group`, `/etc/sudoers`, `/etc/ld.so.preload`, or under `/etc/sudoers.d/`, `/etc/cron.d/` and `/root/.ssh/` | Yes: `TetragonSensitiveFileAccess` |
| `process-creds-changed` | `commit_creds` | A container process that gained a dangerous capability since its exec, or moved into another mount, pid, network or user namespace. The hook runs on every execve and fork; the two selectors keep the exploit signal and drop the rest | Yes: `TetragonCredentialsEscalated` |
| `exec-from-writable-path` | `security_bprm_check` | A container executing a file under `/tmp/`, `/var/tmp/`, `/dev/shm/`, `/run/`, `/var/run/`, `/shared/`, `/controller/` or `/plugins/`, except the three binaries an init container copies there | Yes: `TetragonExecFromWritablePath` |
| `library-from-writable-path` | `security_mmap_file` | The same paths mapped with `PROT_EXEC`, which is how a dropped shared library loads | Yes: `TetragonLibraryFromWritablePath` |
| `privileges-raise` | `create_user_ns`, the `__sys_set*uid` and `__sys_set*gid` family | A user namespace created without `CAP_SYS_ADMIN`; any setuid or setgid to root. Only the first alerts: root re-asserting root is routine (busybox applets, `logrotate`), and a setuid binary that actually raises privileges is `TetragonPrivilegedExec`. `runc`, which sets the ids on every container start, is dropped in the kernel | Yes: `TetragonUserNamespaceCreated` |
| `bpf-program-load` | `bpf_check` | Any BPF program load from a container; the alert leaves out `cilium` and `kube-system` | Yes: `TetragonBpfProgramLoaded` |
| `kernel-module-load` | `security_kernel_module_request`, `security_kernel_read_file`, `find_module_sections` | A module requested or read from a container; the alert leaves out `rook-ceph`, whose CSI plugin loads `rbd` after a boot. The third hook records every load's signature check, host included | Yes: `TetragonKernelModuleLoaded`; `TetragonUnsignedKernelModule` from Loki |
| `dns-outside-cluster` | `ip_output` | A port 53 packet from a container to anything but the kube-dns ClusterIP; the alert leaves out `kube-system`, where CoreDNS forwards upstream | Yes: `TetragonDnsOutsideCluster` |
| `mount-in-container` | `security_sb_mount` | Any mount from a container except by `runc`; the alert leaves out `rook-ceph`, `cilium` and `kube-system`, which mount by design | Yes: `TetragonMountInContainer` |
| `egress-outside-cluster` | `security_socket_connect` | An IPv4 `connect()`, TCP or UDP, from a container to anything outside the pod, service and site ranges | No; ACME, S3, the Trivy database and Home Assistant all do this routinely |

The allow list in the first policy is the set of things that read key material
in steady state, found by listing what fired. Add a binary there only after
confirming it in the export; a person running `cat` on a node is exactly what
the policy is for.

The third policy is the runtime half of an image check: nothing in an image
lives on those paths, so a binary executing from one was written after the
container started. An emptyDir can be mounted anywhere, so the policy names
the mounts this cluster puts binaries into rather than every emptyDir;
`kubectl get pods -A -o json` lists the rest. A `readOnlyRootFilesystem`
pod makes the check complete, since then a new binary has nowhere else to
land. The offline half, every binary Tetragon saw executed diffed against
the image's file list, needs no policy: query Loki by image and binary and
resolve each path in `crane export <image> - | tar t`.

Four more detections from the same library need no policy, because the exec
event already carries what they look for: `sudo`, a setuid or file-capability
binary raising privileges, a fileless exec and a deleted binary. They are
[Loki alerts](logging.md#alerting). Upstream's `sshd` policy is covered by
`NodeSshLogin` from the node journal.

## Usage

The dashboard is the summary; the queries below are what its Loki panels run.

```logql
# Everything a policy caught
{namespace="kube-system", container="export-stdout"} | json | process_kprobe_policy_name!=""

# Every exec in one namespace, with arguments
{namespace="kube-system", container="export-stdout"} | json | process_exec_process_pod_namespace="nextcloud"
  | line_format "{{.process_exec_process_binary}} {{.process_exec_process_arguments}}"
```

The `tetra` CLI in the agent pod does the same live:

```bash
kubectl -n kube-system exec ds/tetragon -c tetragon -- tetra getevents -o compact --namespaces nextcloud
```

## Health check

```bash
kubectl -n kube-system get ds tetragon
kubectl get tracingpolicies -o wide     # STATE should be enabled
```

## Pitfalls

!!! warning "A first hit is a new series, and `increase()` of one sample is zero"
    `tetragon_policy_events_total` has a pod label, so the first hit from a
    pod creates a counter series inside the alert window and `increase()`
    reports nothing until the second scrape moves it. Every policy alert
    therefore also matches a series absent ten minutes ago, with
    `unless ... offset 10m`. Copy that shape for a new alert, and test it
    with a pod that fires once, not with one that keeps firing.

!!! warning "A policy that does not load is silent"
    A `TracingPolicy` naming a kernel function this kernel does not export sits in state `error` and watches nothing. `TetragonPolicyNotLoaded` fires after ten minutes; check `kubectl get tracingpolicies -o wide` after every Flatcar release that moves the kernel.
