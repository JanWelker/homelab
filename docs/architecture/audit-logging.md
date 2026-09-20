---
description: "What the API server records, the policy that decides it, where the log lives, and how to query it in Loki."
---

# Audit Logging

The API server records who did what, to which object, and whether it was
allowed. This page covers what is recorded, the policy that decides it, where
the log ends up, and how to read it.

## At a glance

| | |
| --- | --- |
| Policy file | `/etc/kubernetes/audit/policy.yaml`, written by Ignition from `ansible/templates/butane_node_config.yaml.j2` |
| Log file | `/var/log/kubernetes/audit/audit.log` on each control-plane node |
| Durable copy | [Loki](../platform/logging.md), via Alloy, on Ceph |
| Query it with | `{job="kubernetes-audit"}` in Grafana |
| Worst-case disk | ~1.1 GB per control-plane node |

Until this was configured the API server recorded none of it: `--audit-log-path`
was unset, so no audit log existed at all.

That absence had a second consequence that is easy to miss. Every namespace in
[`pod-security.yaml`](../platform/security-policies.md) carries an `audit` label
set to a stricter level than it enforces — and the destination for an `audit`
finding is the API server audit log. Without one, half of that design was
writing to nowhere. The `warn` half still reached whoever ran `kubectl apply`;
the record nobody was watching in real time, which is the half that matters
afterwards, did not exist.

## The policy decides everything

`--audit-log-path` on its own does nothing. Without `--audit-policy-file` the
API server declines to open a log, and the failure is silent — no error, no
file, an audit configuration that looks present in the manifest and produces
nothing. The policy lives in
`ansible/templates/butane_node_config.yaml.j2` and is written by Ignition to
`/etc/kubernetes/audit/policy.yaml`.

Rules are evaluated top to bottom and **the first match wins**, so the order is
the design:

| Matched | Level | Why |
| --- | --- | --- |
| `/healthz*`, `/livez*`, `/readyz*`, `/version`, `/metrics`, `/openapi*` | `None` | Polled continuously by kubelets, probes and Prometheus; together the majority of requests the cluster serves |
| Leases, Events | `None` | Renewed every few seconds by every component; would bury everything else |
| Reads by the control plane and by `system:nodes` | `None` | Not the reads anyone goes looking for — and dropping them is what makes the next row affordable |
| Secrets, ConfigMaps, TokenReviews | `Metadata` | Reads included: a *read* is the interesting verb, and a write-only policy misses it |
| `pods/exec`, `pods/attach`, `pods/portforward` | `RequestResponse` | The difference between knowing someone exec'd into a pod and knowing which pod, as which user, running what |
| Everything else read-only | `None` | |
| Everything that changes state | `Metadata` | Also the level that carries the Pod Security Admission annotations |

!!! danger "Never raise the Secret rule above `Metadata`"
    At `Request` or `RequestResponse` the request body is recorded, and for a
    Secret the body *is* the credential. The audit log would become a second,
    unencrypted copy of every Secret in the cluster, in a flat file, sitting
    beside the etcd that was [encrypted at rest](security.md#encryption-at-rest) to prevent
    precisely that. It is a one-word change and it undoes the section above it.

## Where the log actually lives

The audit log is written to the root filesystem, which on an installed node is
50 GB of ext4 rather than the tmpfs the nodes used to run from. That is what
makes the CIS rotation numbers affordable:

| Property | Value |
| --- | --- |
| Path | `/var/log/kubernetes/audit/audit.log` |
| Filesystem | root, ext4, 50 GB |
| `--audit-log-maxsize` | `100` (MB) |
| `--audit-log-maxbackup` | `10` |
| `--audit-log-maxage` | `30` (days) |
| Worst-case footprint | ~1.1 GB of disk per control-plane node |
| Durable copy | [Loki](../platform/logging.md), on Ceph |

The filesystem is written once, on the install, and persists from then on — so
the audit log now survives a reboot on its own.
Alloy still tails it into Loki, and that is still where the copy that matters
lives: an audit log stored only on the node is unavailable in precisely the
situation where the node is what failed, and unqueryable next to everything
else in the meantime.

!!! note "This used to be a CIS deviation"
    `--audit-log-maxbackup` was `2` when `/var/log` was part of the tmpfs root
    the nodes ran from, because ten 100 MB files would have reserved 1.1 GB of
    the RAM etcd was running in. [Installing to disk](../operations/nodes.md#rebuilding-or-repartitioning-a-node)
    removed the objection, and check 1.2.18 now passes along with 1.2.16,
    1.2.17 and 1.2.19.

## Reading it

Audit events reach Loki as JSON with `job="kubernetes-audit"`, plus `verb` and
`audit_level` as labels. Everything else stays in the line, where `| json`
can reach it:

```logql
# Who read Secrets, and which ones
{job="kubernetes-audit"} | json | objectRef_resource="secrets"

# Every exec into a running container
{job="kubernetes-audit", verb="create"} | json | objectRef_subresource="exec"

# Pod Security Admission violations that were audited but not enforced
{job="kubernetes-audit"} |= "pod-security.kubernetes.io/audit-violations"
```

That last query is the one the PSA labels were always meant to feed. It answers
"what would break if I tightened `enforce` on this namespace" from evidence
rather than from a dry run, and it answers it for the whole retention window
rather than for the moment you happened to look.

## Applying it to a running cluster

Changing the templates changes what a **newly provisioned** node gets. A running
control-plane node keeps the `kube-apiserver.yaml` static pod manifest that
kubeadm rendered at init time, and nothing rewrites it on its own.

!!! danger "Get the policy file there before you re-render the manifest"
    An API server started with `--audit-policy-file` pointing at a file that does not exist does not start. On a three-node control plane that is survivable; doing it to all three at once is not. Step 2 before step 3, and one node at a time, confirming the API server comes back before moving on.

```bash
# 1. Regenerate the Ignition configs so a rebuild gets this too
make config

# 2. Place the policy file
ssh core@<node> sudo mkdir -p /etc/kubernetes/audit /var/log/kubernetes/audit
ssh core@<node> sudo chmod 0700 /etc/kubernetes/audit /var/log/kubernetes/audit
scp output/.../policy.yaml core@<node>:/tmp/policy.yaml
ssh core@<node> sudo install -m 0600 /tmp/policy.yaml /etc/kubernetes/audit/policy.yaml

# 3. Re-render the API server static pod from the updated config
ssh core@<node> sudo kubeadm init phase control-plane apiserver \
  --config /opt/kubeadm-config.yaml

# 4. The kubelet restarts the static pod within seconds
kubectl -n kube-system get pod kube-apiserver-<node> -w
```

Then update the `kubeadm-config` ConfigMap in `kube-system` so the flags survive
the next `kubeadm upgrade` or control-plane join — the ConfigMap is what kubeadm
reads on those paths, not `/opt/kubeadm-config.yaml`.
