---
description: "The trust boundary this cluster assumes, the tradeoffs made to get there, and what is deliberately not enforced."
---

# Security Posture

This is a homelab on a private network, and several deliberate shortcuts follow
from that. They are listed here so the assumptions are explicit rather than
implied — someone reading the manifests should be able to tell a decision from
an oversight.

That distinction is the whole reason this page exists. Every system carries
weaknesses; the dangerous ones are the weaknesses nobody chose.

## Assumed trust boundary

The cluster assumes a **trusted L2 network segment**. Anything with a port on
that segment is treated as friendly. There is no VPN requirement, no mutual TLS
between components, and no network segmentation inside the cluster.

Worth being clear-eyed about what "trusted" means in a house: it includes the
guest laptop, the smart TV, and the doorbell running firmware from 2019 that
nobody has thought about since. The boundary is real, it is just not as tidy as
the phrase suggests.

The published hostnames are a partial exception. Certificates are issued by
Let's Encrypt through a DNS-01 challenge against a public zone, so
`argo.infra.k8s.wlkr.ch` and its siblings are publicly resolvable names and
appear in Certificate Transparency logs, even though they point at RFC1918
addresses that are unreachable from outside the LAN. Your internal hostnames are
public knowledge the moment you request a certificate for them; plan names
accordingly.

## Provisioning

Provisioning is the least protected phase, by design — it has to work before
any of the cluster's own security exists. This is the classic bootstrap problem,
and everyone solves it the same way: briefly, and with the door open.

| Property | Detail |
| --- | --- |
| Ignition configs are served unauthenticated over HTTP | Anything on the segment can fetch `http://<boot-server>:8000/ignition-<host>.json` while the boot server is running |
| Those configs embed join credentials | The inlined kubeadm config carries the bootstrap `token` and the `certificateKey`, which together are enough to join a new control-plane node |
| Nodes join with `--discovery-token-unsafe-skip-ca-verification` | A joining node does not verify the API server's CA |
| Sysext transfers set `Verify=false` | System extension images are fetched over HTTPS but their signatures are not checked |

The OS image is the exception, and deliberately so. `flatcar-install` is given
`-b`/`-V` rather than a local file, so the node downloads the image *and* its
detached signature from the boot server and checks both against Flatcar's
signing key before writing anything. Serving it over plain HTTP on a segment
this page calls only conditionally trusted is fine precisely because a
substituted image fails the signature check. It is the one artifact on that
server that becomes the operating system, which is why it gets the treatment the
sysexts still do not.

The practical mitigation is time: `make serve` is a foreground command, the
bootstrap token has a 24 hour TTL, and the uploaded certificate key expires
after two hours. **Stop the boot server when provisioning is finished** — it is
the only thing keeping those credentials off the network. A `make serve` left
running in a forgotten tmux session for three months is a genuinely bad outcome,
and it is an easy one to reach.

!!! note "Stopping it is now free"
    Flatcar is installed to disk, so a running node reboots, updates and rejoins with the boot server switched off — the exposure above exists only during a build. That was not true when the nodes ran from RAM and PXE-booted on every restart, which made "stop the boot server" and "let Kured reboot a node at 02:00" mutually exclusive instructions. See [Boot & Bootstrap Process](boot-process.md#every-boot-after-the-first).

`output/credentials/` holds the generated bootstrap token and certificate key in
plaintext. The directory is `0700` and `output/` is gitignored, but the values
are reused across `make config` runs — the Ansible `password` lookup reads back
an existing file rather than regenerating. Delete them to force new ones.

## Secrets

Secrets live in [OpenBao](../platform/openbao.md) and reach workloads as native
Kubernetes `Secret` objects through the
[External Secrets Operator](../platform/external-secrets.md). Nothing sensitive
is committed to Git.

Two consequences worth knowing:

- OpenBao is sealed with Shamir and
  [unsealed by hand](../platform/openbao.md#unsealing-after-a-restart), so the 5
  key shares are the root of trust for every other secret and the only thing that
  brings the store back after a restart. They exist only wherever the operator
  put them; losing all of them loses everything, and nothing outside the cluster
  holds a copy. The cost is a manual step after every reboot — see
  [the resulting limitation](limitations.md#openbao-needs-an-operator-to-unseal-it).
- A Kubernetes `Secret` is base64, not encryption. Anyone with `get secrets` in
  a namespace can read what ESO materialised there. Encryption at rest, below,
  does nothing about this — it protects the bytes in etcd, not the API. If you
  remember one thing from this page, make it this one; the number of people who
  believe otherwise is remarkable.

### Encryption at rest

The API server is configured with an `EncryptionConfiguration` that encrypts
`secrets` with `secretbox` before they reach etcd
(`ansible/templates/kubeadm.yaml.j2`, and the key file in
`ansible/templates/butane_node_config.yaml.j2`). Without it a Secret sits in the etcd
data directory as plaintext, so an etcd backup, a stolen disk, or read access to
`/var/lib/etcd` yields every credential the cluster holds. `strings` on an
unencrypted etcd file is a memorable demonstration, and one worth doing exactly
once, on a cluster you do not care about.

| Property | Detail |
| --- | --- |
| Provider | `secretbox`, with `identity` listed after it |
| Key | 32 random bytes, generated once by `make config` into `output/credentials/encryption_key` |
| Scope | `secrets` only; ConfigMaps and other resources are unencrypted |
| Distribution | The same key on every control-plane node, written by Ignition to `/etc/kubernetes/enc/encryption-config.yaml` (mode `0600`) |

Two things follow from `identity` being listed last. New writes are encrypted,
and Secrets written *before* this was enabled stay readable — they are not
rewritten automatically. To encrypt what already exists, rewrite every Secret
in place once the API servers have restarted:

```bash
kubectl get secrets -A -o json | kubectl replace -f -
```

The key is a single static key with no rotation, and it lives beside the
kubeadm token and certificate key in `output/credentials/`. That directory is
now the thing to protect: it holds the material that decrypts etcd. A KMS
provider would remove the static key, at the cost of a dependency the cluster
must reach before it can serve Secrets.

## Audit logging

The API server records who did what, to which object, and whether it was
allowed. Until this was configured it recorded none of it: `--audit-log-path`
was unset, so no audit log existed at all.

That absence had a second consequence that is easy to miss. Every namespace in
[`pod-security.yaml`](../platform/security-policies.md) carries an `audit` label
set to a stricter level than it enforces — and the destination for an `audit`
finding is the API server audit log. Without one, half of that design was
writing to nowhere. The `warn` half still reached whoever ran `kubectl apply`;
the record nobody was watching in real time, which is the half that matters
afterwards, did not exist.

### The policy decides everything

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
    beside the etcd that was [encrypted at rest](#encryption-at-rest) to prevent
    precisely that. It is a one-word change and it undoes the section above it.

### Where the log actually lives

The audit log is written to the root filesystem, which on an installed node is
125 GB of ext4 rather than the tmpfs the nodes used to run from. That is what
makes the CIS rotation numbers affordable:

| Property | Value |
| --- | --- |
| Path | `/var/log/kubernetes/audit/audit.log` |
| Filesystem | root, ext4, 125 GB |
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
    the RAM etcd was running in. [Installing to disk](../operations/index.md#repartitioning-the-nodes)
    removed the objection, and check 1.2.18 now passes along with 1.2.16,
    1.2.17 and 1.2.19.

### Reading it

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

### Applying it to a running cluster

Changing the templates changes what a **newly provisioned** node gets. A running
control-plane node keeps the `kube-apiserver.yaml` static pod manifest that
kubeadm rendered at init time, and nothing rewrites it on its own.

One node at a time, confirming the API server comes back before moving to the
next:

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

!!! warning "Get the policy file there first"
    An API server started with `--audit-policy-file` pointing at a file that
    does not exist does not start. On a three-node control plane that is
    survivable; doing it to all three at once is not. Step 2 before step 3, and
    one node at a time.

## Authorization

**ArgoCD AppProjects do not constrain much.** `payload/argocd/argocd-projects.yaml`
defines three projects, but `apps` and `infra` both allow `sourceRepos: "*"` and
a `clusterResourceWhitelist` of every group and kind, in every namespace. Only
`system` restricts its destination namespace.

They are useful as grouping and as a place to add restrictions later. They are
not an isolation boundary today: an Application in the `apps` project can create
cluster-scoped RBAC. Which is to say, a workload's manifest directory can quietly
grant itself the keys to the cluster, and nothing would object.

**Network policy covers four namespaces.** `openbao`, `cert-manager`,
`external-secrets` and `monitoring` have default-deny **ingress**
`CiliumNetworkPolicy` rules; every other namespace, and all egress everywhere,
is still unrestricted. See [Security Policies](../platform/security-policies.md).

**Pod Security Admission is on, but mostly auditing.** Every platform namespace
carries `enforce` at the level it demonstrably needs and `warn`/`audit` at a
stricter one, so violations are visible without breaking what runs today. This is
the sane order of operations: measure first, enforce second. Enforcing first is
how you end up disabling the control entirely at 2am. The `audit` half of that
now has a destination — see [Audit logging](#audit-logging).

**Both Gateways admit routes from every namespace** (`allowedRoutes.namespaces.from: All`).
Any namespace can attach an `HTTPRoute` to `infra-gateway` and claim a hostname
under `*.infra.k8s.wlkr.ch`.

## Exposed interfaces

Every platform UI on the infra gateway is behind
[Authentik](../platform/authentik.md), by one of two routes:

| Service | Authentication |
| --- | --- |
| ArgoCD | Authentik OIDC; local admin disabled. The server runs with `--insecure` because TLS terminates at the Gateway |
| Grafana | Authentik OIDC; login form disabled |
| Hubble UI | Authentik proxy outpost |
| Rook dashboard | Authentik proxy outpost |
| Prometheus | Authentik proxy outpost |
| Alertmanager | Authentik proxy outpost |
| OpenBao UI | Token or configured auth method — not behind Authentik |

Two things follow. Authentik is now a dependency of reaching any of them, so
the break-glass paths in
[When Authentik is down](../platform/authentik.md#when-authentik-is-down) matter.
And OpenBao is deliberately left out: putting the thing that holds Authentik's
own database password behind Authentik would be a loop, and circular
dependencies in an auth stack are only funny from a distance.

## What would tighten this up

Roughly in order of value against effort:

1. Extend default-deny ingress to the namespaces
   [not yet covered](../platform/security-policies.md#scope), then start on
   egress — the larger and more breakable half.
2. Narrow `sourceRepos` on the AppProjects to this repository and the Helm
   repositories actually in use.
3. Restrict `allowedRoutes` on `infra-gateway` to the platform namespaces.
4. Add a second Alertmanager receiver on a different transport. One receiver,
   one mailbox and one SMTP provider means a failure of the mail path is itself
   unmonitored — see
   [Alerting reaches one mailbox](limitations.md#alerting-reaches-one-mailbox).
5. Move etcd encryption to a KMS provider, removing the static
   `encryption_key` that currently sits in `output/credentials/` with no
   rotation.
6. Get the backups out of the cluster. Velero and the etcd snapshots write to
   the Ceph object store they are backing up — see
   [Backups do not leave the cluster](limitations.md#backups-do-not-leave-the-cluster).
   With etcd now persisting across reboots there is more worth losing than
   there used to be.

See [Known Limitations](limitations.md) for the operational counterparts.
