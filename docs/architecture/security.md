---
description: "The trust boundary this cluster assumes, the tradeoffs made to get there, and what is deliberately not enforced."
---

# Security Posture

This is a homelab on a private network, and several deliberate shortcuts follow
from that. They are listed here so the assumptions are explicit rather than
implied — someone reading the manifests should be able to tell a decision from
an oversight.

## Assumed trust boundary

The cluster assumes a **trusted L2 network segment**. Anything with a port on
that segment is treated as friendly. There is no VPN requirement, no mutual TLS
between components, and no network segmentation inside the cluster.

The published hostnames are a partial exception. Certificates are issued by
Let's Encrypt through a DNS-01 challenge against a public zone, so
`argo.infra.k8s.wlkr.ch` and its siblings are publicly resolvable names and
appear in Certificate Transparency logs, even though they point at RFC1918
addresses that are unreachable from outside the LAN.

## Provisioning

Provisioning is the least protected phase, by design — it has to work before
any of the cluster's own security exists.

| Property | Detail |
| --- | --- |
| Ignition configs are served unauthenticated over HTTP | Anything on the segment can fetch `http://<boot-server>:8000/ignition-<host>.json` while the boot server is running |
| Those configs embed join credentials | The inlined kubeadm config carries the bootstrap `token` and the `certificateKey`, which together are enough to join a new control-plane node |
| Nodes join with `--discovery-token-unsafe-skip-ca-verification` | A joining node does not verify the API server's CA |
| Sysext transfers set `Verify=false` | System extension images are fetched over HTTPS but their signatures are not checked |

The practical mitigation is time: `make serve` is a foreground command, the
bootstrap token has a 24 hour TTL, and the uploaded certificate key expires
after two hours. **Stop the boot server when provisioning is finished** — it is
the only thing keeping those credentials off the network.

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

- OpenBao has no auto-unseal, so the unseal keys are the root of trust for every
  other secret and exist only wherever the operator put them.
- A Kubernetes `Secret` is base64, not encryption. Anyone with `get secrets` in
  a namespace can read what ESO materialised there. Encryption at rest, below,
  does nothing about this — it protects the bytes in etcd, not the API.

### Encryption at rest

The API server is configured with an `EncryptionConfiguration` that encrypts
`secrets` with `secretbox` before they reach etcd
(`ansible/templates/kubeadm.yaml.j2`, and the key file in
`ansible/templates/butane_config.yaml.j2`). Without it a Secret sits in the etcd
data directory as plaintext, so an etcd backup, a stolen disk, or read access to
`/var/lib/etcd` yields every credential the cluster holds.

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

## Authorization

**ArgoCD AppProjects do not constrain much.** `payload/argocd/argocd-projects.yaml`
defines three projects, but `apps` and `infra` both allow `sourceRepos: "*"` and
a `clusterResourceWhitelist` of every group and kind, in every namespace. Only
`system` restricts its destination namespace.

They are useful as grouping and as a place to add restrictions later. They are
not an isolation boundary today: an Application in the `apps` project can create
cluster-scoped RBAC.

**Network policy covers four namespaces.** `openbao`, `cert-manager`,
`external-secrets` and `monitoring` have default-deny **ingress**
`CiliumNetworkPolicy` rules; every other namespace, and all egress everywhere,
is still unrestricted. See [Security Policies](../platform/security-policies.md).

**Pod Security Admission is on, but mostly auditing.** Every platform namespace
carries `enforce` at the level it demonstrably needs and `warn`/`audit` at a
stricter one, so violations are visible without breaking what runs today.

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
| Hubble UI | Authentik proxy outpost — previously **none at all** |
| Rook dashboard | Authentik proxy outpost |
| Prometheus | Authentik proxy outpost; not exposed at all before |
| Alertmanager | Authentik proxy outpost; not exposed at all before |
| OpenBao UI | Token or configured auth method — not behind Authentik |

Two things follow. Authentik is now a dependency of reaching any of them, so
the break-glass paths in
[When Authentik is down](../platform/authentik.md#when-authentik-is-down) matter.
And OpenBao is deliberately left out: putting the thing that holds Authentik's
own database password behind Authentik would be a loop.

## What would tighten this up

Roughly in order of value against effort:

1. Configure Alertmanager receivers, so a failure is noticed at all.
2. Add default-deny NetworkPolicies per namespace, starting with the platform
   namespaces.
3. Narrow `sourceRepos` on the AppProjects to this repository and the Helm
   repositories actually in use.
4. Restrict `allowedRoutes` on `infra-gateway` to the platform namespaces.
5. Configure OpenBao auto-unseal against a KMS, removing the manual unseal step
   and the 5-of-N key custody problem.

See [Known Limitations](limitations.md) for the operational counterparts.
