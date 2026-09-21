---
description: "Runtime detection with Tetragon: what it records from every node, which two policies it enforces, and how the events reach Loki and Alertmanager."
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
| Stage | `10-agents`, with Alloy; nothing depends on it |
| Depends on | Kernel BTF at `/sys/kernel/btf/vmlinux`, which Flatcar ships; [Logging](logging.md) for the events, [Monitoring](monitoring.md) for the alerts |
| If it is down | No runtime record and no runtime alerts; nothing else notices |
| Health check | `kubectl get tracingpolicies -o wide`; `TetragonPolicyNotLoaded` and `TetragonEventsLost` otherwise |
| Files | `payload/platform/tetragon/` |

## Configuration

Cilium's own chart, from `helm.cilium.io`; Tetragon does not depend on Cilium
and runs privileged on the host network by the chart's default.

| Setting | Why |
| --- | --- |
| Export to stdout | The chart runs a sidecar that tails Tetragon's JSON file to stdout, so [Alloy](logging.md) collects it as any container log: `{namespace="kube-system", container="export-stdout"}` |
| `exportDenyList` | Exec and exit events from the host, `cilium` and `kube-system` stay out of the export; they are the bulk of the volume and the least interesting. Policy hits from those namespaces still pass, which is the point of the `event_set` field |
| `enableProcessCred` | Capabilities and `privileges_changed` on every exec, so a setuid binary or a file-capability escalation is visible without a policy |
| Two `ServiceMonitor`s | `tetragon_policy_events_total` is what the alert reads; `tetragon_tracingpolicy_loaded` and the ring-buffer counters are its health |

### Policies

`tracingpolicies.yaml` carries two cluster-scoped `TracingPolicy` objects.
Both post at most one event per minute per selector.

| Policy | Hook | Fires on | Alerted |
| --- | --- | --- | --- |
| `sensitive-file-access` | `security_file_permission` | A read under `/etc/kubernetes/pki/`, `/etc/kubernetes/enc/`, `/var/lib/etcd/` or `/var/lib/kubelet/pki/` by anything but the control-plane binaries, `kubeadm` and `kube-vip`; any read of `/etc/shadow` or the admin kubeconfigs by anything but `kube-vip` and `kubeadm` | Yes: `TetragonSensitiveFileAccess` |
| `process-creds-changed` | `commit_creds` | Any credential change in a container, from the upstream example | No; a record for the exec alert to be read against |

The allow list in the first policy is the set of things that read key material
in steady state, found by listing what fired. Add a binary there only after
confirming it in the export; a person running `cat` on a node is exactly what
the policy is for.

## Usage

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

!!! warning "A policy that does not load is silent"
    A `TracingPolicy` naming a kernel function this kernel does not export sits in state `error` and watches nothing. `TetragonPolicyNotLoaded` fires after ten minutes; check `kubectl get tracingpolicies -o wide` after every Flatcar release that moves the kernel.
