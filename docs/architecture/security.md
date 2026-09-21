---
description: "The trust boundary this cluster assumes, the tradeoffs made to get there, and what is deliberately not enforced."
---

# Security Posture

This is a homelab on a private network, and several deliberate shortcuts follow
from that. They are listed so that someone reading the manifests can tell a
decision from an oversight.

## Assumed trust boundary

The cluster assumes a **trusted L2 network segment**: anything with a port on
it is treated as friendly. There is no VPN requirement, no mutual TLS between
components, and no network segmentation inside the cluster.

The published hostnames are a partial exception. Certificates come from Let's
Encrypt through a DNS-01 challenge against a public zone, so
`argo.infra.k8s.wlkr.ch` and its siblings are publicly resolvable and appear in
Certificate Transparency logs, even though they point at RFC1918 addresses. Plan
names accordingly.

## Provisioning

Provisioning is the least protected phase, by design: it has to work before any
of the cluster's own security exists.

| Property | Detail |
| --- | --- |
| Ignition configs are served unauthenticated over HTTP | Anything on the segment can fetch `http://<boot-server>:8000/ignition-<host>.json` while the boot server is running |
| Those configs embed join credentials | The inlined kubeadm config carries the bootstrap `token` and the `certificateKey`, together enough to join a new control-plane node |
| The control-plane configs embed the etcd encryption key | `/etc/kubernetes/enc/encryption-config.yaml` is inlined with the key that encrypts every Secret at rest. It has no TTL: anyone who fetched a control-plane config while the boot server was up can read Secrets out of any etcd backup, for the life of the cluster |
| Nodes join with `--discovery-token-unsafe-skip-ca-verification` | A joining node does not verify the API server's CA |
| Sysext updates set `Verify=false` | On first boot Ignition checks each image against the sha256 the boot server was given, so a substituted image fails. Later sysupdate downloads travel over HTTPS with signature verification off |
| The OS image is signature-checked | `flatcar-install` is given `-b`/`-V`, so the node downloads the image and its detached signature and checks both against Flatcar's key before writing anything |

The mitigation is time: `make serve` is a foreground command, the bootstrap
token has a 24 hour TTL and the certificate key expires after two hours. The
encryption key has no clock, which is why the boot server listens only on
`boot_server_ip`, serves files by name and lists nothing. **Stop the boot
server when provisioning is finished.** Flatcar is installed to disk, so
nothing needs it after a build — see
[Every boot after the first](boot-process.md#every-boot-after-the-first).

`output/credentials/` holds the generated bootstrap token and certificate key
in plaintext. The directory is `0700` and `output/` is gitignored, but the
values are reused across `make config` runs — the Ansible `password` lookup
reads back an existing file. Delete them to force new ones.

## Secrets

Secrets live in [OpenBao](../platform/openbao.md) and reach workloads as native
Kubernetes `Secret` objects through the
[External Secrets Operator](../platform/external-secrets.md). Nothing
sensitive is committed to Git.

- OpenBao is sealed with Shamir and
  [unsealed by hand](../platform/openbao.md#unsealing-after-a-restart), so the
  5 key shares are the root of trust for every other secret; losing all of them
  loses everything, and nothing outside the cluster holds a copy.
- A Kubernetes `Secret` is base64, not encryption. Anyone with `get secrets` in
  a namespace can read what ESO materialised there. Encryption at rest protects
  the bytes in etcd, not the API.

### Encryption at rest

The API server encrypts `secrets` with `secretbox` before they reach etcd
(`ansible/templates/kubeadm.yaml.j2`; the key file in
`ansible/templates/butane_node_config.yaml.j2`). Without it an etcd backup, a
stolen disk, or read access to `/var/lib/etcd` yields every credential the
cluster holds.

| Property | Detail |
| --- | --- |
| Provider | `secretbox`, with `identity` listed after it |
| Key | 32 random bytes, generated once by `make config` into `output/credentials/encryption_key` |
| Scope | `secrets` only; ConfigMaps and other resources are unencrypted |
| Distribution | The same key on every control-plane node, written by Ignition to `/etc/kubernetes/enc/encryption-config.yaml` (mode `0600`) |

With `identity` last, new writes are encrypted and Secrets written before it
was enabled stay readable but are not rewritten. To encrypt what already
exists, once the API servers have restarted:

```bash
kubectl get secrets -A -o json | kubectl replace -f -
```

The key is static, with no rotation, and lives in `output/credentials/` beside
the kubeadm token and certificate key — that directory is what decrypts etcd.
A KMS provider would remove the static key at the cost of a dependency the
cluster must reach before it can serve Secrets.

## Runtime

[Tetragon](../platform/tetragon.md) records every exec in every pod and
alerts when anything but the control plane reads the cluster's key material.
It is detection, not prevention: nothing is killed, and a policy that fails to
load on a new kernel watches nothing until its alert fires.

## Audit logging

The API server records who did what, to which object, and whether it was
allowed; the `audit` half of the Pod Security Admission labels has nowhere to
go without it. Policy, retention and queries are in
[Audit Logging](audit-logging.md).

## Authorization

**AppProjects constrain the workloads, not the platform.**
`payload/platform/argocd-projects/projects.yaml` defines three. `infra` allows
`sourceRepos: "*"` and every group and kind in every namespace: it is this
repository deploying this repository, and a restriction there guards against
nothing the PR review does not. `system` is confined to the `argocd` namespace.
`apps` has the boundary to draw, because its Applications come from a second
repository with a lighter review: it accepts only the workloads repository and
the chart repositories the workloads use, may not write into any platform
namespace (`authentik` excepted, for the blueprint ConfigMap a workload ships
there), and at cluster scope may create namespaces, CRDs, cluster RBAC and
Trivy's compliance reports — what trivy-operator demonstrably needs.

**Network policy covers nine namespaces.** `openbao`, `cert-manager`,
`external-secrets`, `monitoring`, `external-dns`, `kubelet-csr-approver`,
`kured`, `logging` and `cnpg-system` have default-deny **ingress**
`CiliumNetworkPolicy` rules; every other namespace, and all egress everywhere,
is unrestricted. See [Security Policies](../platform/security-policies.md).

**Pod Security Admission is on, but mostly auditing.** Every platform namespace
carries `enforce` at the level it demonstrably needs and `warn`/`audit` at a
stricter one, so violations are visible without breaking what runs: measure
first, enforce second.

**Image references are checked at admission.** An untagged or `latest` image
is refused; a registry outside the allowlist is recorded and warned about but
admitted. Signatures are not verified: few of the upstreams sign, so a
verifier would exempt nearly everything. See
[Security Policies](../platform/security-policies.md#admission-policies).

**Both Gateways admit routes from every namespace**
(`allowedRoutes.namespaces.from: All`). Any namespace can attach an `HTTPRoute`
to `infra-gateway` and claim a hostname under `*.infra.k8s.wlkr.ch`.

## Exposed interfaces

Every platform UI on the infra gateway is behind
[Authentik](../platform/authentik.md):

| Service | Authentication |
| --- | --- |
| ArgoCD | Authentik OIDC; local admin disabled. The server runs with `--insecure` because TLS terminates at the Gateway |
| Grafana | Authentik OIDC; login form disabled |
| Hubble UI | Authentik proxy outpost |
| Rook dashboard | Authentik proxy outpost |
| Prometheus | Authentik proxy outpost |
| Alertmanager | Authentik proxy outpost |
| OpenBao UI | Token or configured auth method — not behind Authentik |

Authentik is therefore a dependency of reaching any of them; the break-glass
paths are in [When Authentik is down](../platform/authentik.md#when-authentik-is-down).
OpenBao is left out deliberately: it holds Authentik's own database password,
so putting it behind Authentik would be a loop.

## What would tighten this up

Roughly in order of value against effort:

1. Extend default-deny ingress to the namespaces
   [not yet covered](../platform/security-policies.md#scope), then start on
   egress — the larger and more breakable half.
2. Narrow `sourceRepos` on the AppProjects to this repository and the Helm
   repositories actually in use.
3. Restrict `allowedRoutes` on `infra-gateway` to the platform namespaces, and
   promote the registry allowlist to `Deny`.
4. Add a second Alertmanager receiver on a different transport, so a failure of
   the mail path is not itself unmonitored.
5. Move etcd encryption to a KMS provider, removing the static
   `encryption_key` in `output/credentials/`.
6. Get the backups out of the cluster: Velero and the etcd snapshots write to
   the Ceph object store they are backing up.

See [Known Limitations](limitations.md) for the operational counterparts.
