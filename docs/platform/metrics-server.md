---
description: "The resource metrics API: why kubectl top and HPAs need it, and how kubelet certificates are verified rather than skipped."
---

# metrics-server

[metrics-server](https://github.com/kubernetes-sigs/metrics-server) serves the
`metrics.k8s.io` API — the resource metrics the scheduler, `kubectl top` and
every `HorizontalPodAutoscaler` read from.

Nothing served that API before. `kubectl top node` returned an error, and any
HPA added to a workload would have sat at `<unknown>/<target>` indefinitely
without ever explaining why — one of those failures that produces no logs, no
events, and no clue, just a dash where a number should be.

!!! note "Prometheus is not a substitute"
    It is easy to assume kube-prometheus-stack covers this. It does not.
    Prometheus scrapes into its own time-series store and answers PromQL. The
    resource metrics API is a separate, aggregated Kubernetes API that core
    controllers read directly. They are different consumers of the same
    underlying kubelet data.

## Verifying the kubelet instead of trusting it

metrics-server scrapes each kubelet over TLS. By default a kubelet serves a
**self-signed** certificate it generates itself, which metrics-server cannot
verify — so the near-universal shortcut is to run it with
`--kubelet-insecure-tls` and skip verification entirely. It is in every quickstart,
it is in most production clusters, and it makes the metrics path spoofable by
anything that can occupy a kubelet's address. It is also, to be fair, the flag
that makes the thing work in five minutes instead of an afternoon.

This cluster does it properly instead, which takes three pieces:

| Piece | Where | What it does |
| --- | --- | --- |
| `serverTLSBootstrap: true` | `ansible/templates/kubeadm.yaml.j2` | The kubelet requests a serving certificate from the cluster CA instead of self-signing |
| kubelet-csr-approver | `payload/platform/kubelet-csr-approver/` | Approves those CSRs, under constraints |
| `--kubelet-certificate-authority` | `payload/platform/metrics-server/` | metrics-server verifies against the cluster CA |

### Why the CSRs need an approver at all

Kubernetes deliberately does **not** auto-approve
`kubernetes.io/kubelet-serving` CSRs. Approving them blindly would let a
compromised node request a certificate for any name or address it liked, and
then impersonate another node. So the CSRs sit `Pending` forever unless
something decides — which is the correct default and also why so many clusters
end up reaching for `--kubelet-insecure-tls` and never looking back.

[kubelet-csr-approver](https://github.com/postfinance/kubelet-csr-approver)
is that something, and it is constrained rather than permissive:

```yaml
providerRegex: "^(odin|thor|loki|freya|heimdall|valkyrie)$"
providerIpPrefixes: [10.9.2.0/24]
maxExpirationSeconds: 86400
bypassDnsResolution: true
```

- Only those six node names are approvable. Anything else is **denied**, not
  ignored.
- The requested IP must be on the nodes' subnet.
- Certificates last a day, so a leaked one expires on its own.
- DNS resolution is bypassed because node names do not resolve here — they come
  from the Ansible inventory, not a resolver. The name regex and IP prefix are
  what constrain identity in its place.

### Checking it worked

```bash
kubectl get csr | grep kubelet-serving   # should be Approved,Issued
kubectl top nodes
```

If CSRs are `Pending`, kubelet-csr-approver is not running or a node name does
not match the regex. If they are `Denied`, the regex or the IP prefix is wrong —
check the controller's logs before widening either.

!!! warning "Adding a node means editing the regex"
    A node whose name is not in `providerRegex` will have its CSR denied, keep its self-signed certificate, and go silently missing from `kubectl top` — the node is fine, the cluster is fine, and one row is simply absent from a table nobody reads carefully. Add it to the regex at the same time you add it to the inventory; the regex lives in `payload/platform/kubelet-csr-approver/application.yaml`.

## Directory Structure

```text
metrics-server/
└── application.yaml         # ArgoCD Application (Helm: metrics-server)

kubelet-csr-approver/
└── application.yaml         # ArgoCD Application (Helm: kubelet-csr-approver)
```
