---
description: "What the API server records, the policy that decides it, where the log lives, and how to query it in Loki."
---

# Audit Logging

The API server records who did what, to which object, and whether it was
allowed. It is also the destination for the `audit` half of the Pod Security
Admission labels in [`pod-security.yaml`](../platform/security-policies.md):
without it, findings at the stricter audit level go nowhere.

## At a glance

| | |
| --- | --- |
| Policy file | `/etc/kubernetes/audit/policy.yaml`, written by Ignition from `ansible/templates/butane_node_config.yaml.j2` |
| Log file | `/var/log/kubernetes/audit/audit.log` on each control-plane node |
| Durable copy | [Loki](../platform/logging.md), via Alloy, on Ceph |
| Query it with | `{job="kubernetes-audit"}` in Grafana |
| Worst-case disk | About 1 GB per control-plane node — see [Where the log lives](#where-the-log-lives) |

## The policy decides everything

`--audit-log-path` on its own does nothing: without `--audit-policy-file` the
API server declines to open a log, silently. Rules are evaluated top to bottom
and **the first match wins**, so the order is the design:

| Matched | Level | Why |
| --- | --- | --- |
| `/healthz*`, `/livez*`, `/readyz*`, `/version`, `/metrics`, `/openapi*` | `None` | Polled continuously by kubelets, probes and Prometheus; the majority of requests |
| Leases, Events | `None` | Renewed every few seconds by every component |
| Reads by the control plane and by `system:nodes` | `None` | Not the reads anyone looks for; dropping them makes the next row affordable |
| Secrets, ConfigMaps, TokenReviews | `Metadata` | Reads included: a *read* is the interesting verb |
| `pods/exec`, `pods/attach`, `pods/portforward` | `RequestResponse` | Which pod, as which user, running what |
| Everything else read-only | `None` | |
| Everything that changes state | `Metadata` | Also the level that carries the Pod Security Admission annotations |

!!! danger "Never raise the Secret rule above `Metadata`"
    At `Request` or `RequestResponse` the request body is recorded, and for a Secret the body *is* the credential. The audit log would become an unencrypted copy of every Secret in the cluster, in a flat file beside the etcd that was [encrypted at rest](security.md#encryption-at-rest) to prevent exactly that.

## Where the log lives

| Property | Value |
| --- | --- |
| Path | `/var/log/kubernetes/audit/audit.log` |
| Filesystem | root, ext4 |
| Rotation | The `--audit-log-maxsize`, `--audit-log-maxbackup` and `--audit-log-maxage` flags in `ansible/templates/kubeadm.yaml.j2`; the product of size and backups bounds the footprint to about 1 GB, and the age cap keeps a quiet cluster from holding a month-old rotation forever |

The root filesystem persists across reboots, so the log survives on the node.
Alloy still tails it into Loki, which is the copy that matters: a log stored
only on the node is unavailable in precisely the situation where the node is
what failed.

## Reading it

Audit events reach Loki as JSON with `job="kubernetes-audit"`, plus `verb` and
`audit_level` as labels. Everything else stays in the line, where `| json` can
reach it:

```logql
# Who read Secrets, and which ones
{job="kubernetes-audit"} | json | objectRef_resource="secrets"

# Every exec into a running container
{job="kubernetes-audit", verb="create"} | json | objectRef_subresource="exec"

# Pod Security Admission violations that were audited but not enforced
{job="kubernetes-audit"} |= "pod-security.kubernetes.io/audit-violations"
```

The last query answers "what would break if I tightened `enforce` on this
namespace" from evidence, for the whole retention window. Exec, Secret reads
by a person, RBAC writes, 403 bursts and anonymous requests also fire alerts
from Loki's ruler — see [Logging](../platform/logging.md#alerting).

## Applying it to a running cluster

Changing the templates changes what a **newly provisioned** node gets. A
running control-plane node keeps the `kube-apiserver.yaml` static pod manifest
kubeadm rendered at init time, and nothing rewrites it on its own.

!!! danger "Get the policy file there before you re-render the manifest"
    An API server started with `--audit-policy-file` pointing at a missing file does not start. Step 3 before step 4, one node at a time, confirming the API server comes back before moving on.

1. Regenerate the Ignition configs so a rebuild gets this too:

    ```bash
    make config
    ```

2. Create the directories on the node:

    ```bash
    ssh core@<node> sudo mkdir -p /etc/kubernetes/audit /var/log/kubernetes/audit
    ssh core@<node> sudo chmod 0700 /etc/kubernetes/audit /var/log/kubernetes/audit
    ```

3. Place the policy file:

    ```bash
    scp output/.../policy.yaml core@<node>:/tmp/policy.yaml
    ssh core@<node> sudo install -m 0600 /tmp/policy.yaml /etc/kubernetes/audit/policy.yaml
    ```

4. Re-render the API server static pod from the updated config:

    ```bash
    ssh core@<node> sudo kubeadm init phase control-plane apiserver \
      --config /opt/kubeadm-config.yaml
    ```

5. Watch the kubelet restart the static pod:

    ```bash
    kubectl -n kube-system get pod kube-apiserver-<node> -w
    ```

6. Update the `kubeadm-config` ConfigMap in `kube-system` so the flags survive
   the next `kubeadm upgrade` or control-plane join — kubeadm reads the
   ConfigMap on those paths, not `/opt/kubeadm-config.yaml`:

    ```bash
    kubectl -n kube-system edit configmap kubeadm-config
    ```
