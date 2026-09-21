---
description: "The resource metrics API: why kubectl top and HPAs need it, and how kubelet certificates are verified rather than skipped."
---

# metrics-server

[metrics-server](https://github.com/kubernetes-sigs/metrics-server) serves the
`metrics.k8s.io` API that the scheduler, `kubectl top` and every
`HorizontalPodAutoscaler` read. Without it `kubectl top` errors and an HPA sits
at `<unknown>/<target>` with no logs, events or clue. Prometheus is not a
substitute: it answers PromQL from its own store, while the resource metrics
API is an aggregated Kubernetes API that core controllers read directly.

## At a glance

| | |
| --- | --- |
| Namespace | `kube-system`; kubelet-csr-approver in `kubelet-csr-approver` |
| Stage | `08-services`; kubelet-csr-approver in `03-controllers`, because the CSRs have to be approved first |
| Depends on | kubelet-csr-approver, or the kubelet certificates it verifies are never issued |
| If it is down | `kubectl top` and every HPA. Nothing else notices |
| Health check | `kubectl top nodes` &rarr; a row per node |
| Files | `payload/platform/metrics-server/`, `payload/platform/kubelet-csr-approver/` |

## Configuration

### Verifying the kubelet instead of trusting it

A kubelet serves a self-signed certificate by default, so the usual shortcut
is `--kubelet-insecure-tls`, which makes the metrics path spoofable by anything
that can occupy a kubelet's address. Kubernetes deliberately does not
auto-approve `kubernetes.io/kubelet-serving` CSRs, since a compromised node
could request a certificate for any name, so doing it properly takes three
pieces:

| Piece | Where | What it does |
| --- | --- | --- |
| `serverTLSBootstrap: true` | `ansible/templates/kubeadm.yaml.j2` | The kubelet requests a serving certificate from the cluster CA |
| [kubelet-csr-approver](https://github.com/postfinance/kubelet-csr-approver) | `payload/platform/kubelet-csr-approver/application.yaml` | Approves those CSRs, constrained by `providerRegex` (only the inventory's node names; anything else is **denied**), `providerIpPrefixes` (the nodes' subnet), a one-day `maxExpirationSeconds`, and `bypassDnsResolution` because node names come from the Ansible inventory, not a resolver |
| `--kubelet-certificate-authority` | `payload/platform/metrics-server/application.yaml` | metrics-server verifies against the cluster CA |

| Setting in `metrics-server/application.yaml` | Why |
| --- | --- |
| Flags via `defaultArgs`, not `args` | The default list already sets `--kubelet-preferred-address-types`, and a flag passed twice leaves the winner to parsing order. It is `InternalIP` because node hostnames do not resolve here |
| Two replicas and a PodDisruptionBudget | A node reboot does not take the resource metrics API down |

## Health check

```bash
kubectl get csr | grep kubelet-serving   # should be Approved,Issued
kubectl top nodes
```

`Pending` CSRs mean kubelet-csr-approver is not running or a node name does not
match the regex. `Denied` means the regex or the IP prefix is wrong — check the
controller's logs before widening either.

## Pitfalls

!!! warning "Adding a node means editing the regex"
    A node missing from `providerRegex` has its CSR denied, keeps its self-signed certificate, and silently goes missing from `kubectl top`. Add it to the regex when you add it to the inventory.
