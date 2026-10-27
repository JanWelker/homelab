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
| Those configs embed join credentials | The bootstrap `token`, and on control-plane nodes the `certificateKey`, together enough to join a new control-plane node. Only the init host has them in its kubeadm config; the others get a `0600` environment file that `bootstrap-k8s.service` names by variable and deletes after a successful join, so `systemctl cat` and `systemctl show` reveal nothing |
| The control-plane configs embed the etcd encryption key | `/etc/kubernetes/enc/encryption-config.yaml` is inlined with the key that encrypts every Secret at rest. It has no TTL: anyone who fetched a control-plane config while the boot server was up can read Secrets out of any etcd backup, for the life of the cluster |
| Nodes join with `--discovery-token-unsafe-skip-ca-verification` | A joining node does not verify the API server's CA |
| Sysext updates set `Verify=false` | On first boot Ignition checks each image against the sha256 the boot server was given, so a substituted image fails. Later sysupdate downloads travel over HTTPS with signature verification off. The transfer config that names their source is inlined from a template in Git, not fetched |
| The PXE kernel, initrd and bootloader are signature-checked | `make download` takes their sums only from a clearsigned file that `gpgv` verifies against a pinned primary key fingerprint: Flatcar's image signing key for `.DIGESTS.asc`, the kernel.org checksum autosigner for syslinux's `sha256sums.asc` |
| The OS image is signature-checked | `flatcar-install` is given `-b`/`-V`, so the node downloads the image and its detached signature and checks both against Flatcar's key before writing anything |

The mitigation is time: `make serve` is a foreground command, the bootstrap
token has a 24 hour TTL and the certificate key expires after two hours, and
`make config` re-arms both only for those windows. The
encryption key has no clock, which is why the HTTP server listens only on
`boot_server_ip`, serves files by name and lists nothing. TFTP listens on
every interface, because macOS lets an unprivileged process bind port 69 only
there, and refuses every write request, so nobody can plant a boot menu.
**Stop the boot
server when provisioning is finished.** Flatcar is installed to disk, so
nothing needs it after a build — see
[Every boot after the first](boot-process.md#every-boot-after-the-first).

`output/credentials/` holds the generated bootstrap token and certificate key
in plaintext. The directory is `0700`, the Ignition configs in `output/http`
are `0600`, and `output/` is gitignored. The values are reused across
`make config` runs, because a node whose join window passed retries with the
values on its disk once `make config` has re-armed them
([Adding a node](../operations/nodes.md#adding-a-node)). Delete them to force
new ones. The installer keeps its copy of the node config `0600` in RAM;
`flatcar-install` carries that mode to the OEM partition.

## Node hardening

`ansible/templates/kubeadm.yaml.j2` and `ansible/templates/butane_node_config.yaml.j2`
carry the settings below. They land at provisioning time and change only on
`make reinstall`.

| Setting | Why |
| --- | --- |
| `proxy.disabled: true` | Cilium replaces `kube-proxy`. The field lands in the `kubeadm-config` ConfigMap that `kubeadm upgrade apply` reads; a `skipPhases` on init alone leaves `proxy: {}` stored, and every upgrade puts `kube-proxy` back beside Cilium |
| `--kubelet-certificate-authority` | The API server verifies the kubelet's serving certificate for exec, logs, attach and port-forward, the calls that carry command output. It can, because `serverTLSBootstrap` makes the kubelet request its certificate from the cluster CA and [kubelet-csr-approver](../platform/metrics-server.md) approves the CSR; metrics-server verifies the same certificates |
| `--admission-control-config-file` | A [Pod Security default](../platform/security-policies.md#pod-security-admission) for namespaces without labels, so a namespace nobody labelled is `baseline`, not `privileged` |
| `--profiling=false` on the API server, controller-manager and scheduler | `/debug/pprof` exposes internals to any token with the right RBAC and helps nothing here; CIS asks for it off |
| Metrics listeners on the node address | etcd's plain-HTTP metrics and the controller-manager and scheduler bind the node's own address, where Prometheus scrapes them, instead of every interface. The address differs per node, so it comes from per-node JSON patches in `/etc/kubernetes/patches` rather than the cluster-wide `ClusterConfiguration`; the patches also move the probes, and etcd keeps `127.0.0.1` for its own |
| `DenyServiceExternalIPs` | `spec.externalIPs` lets whoever can create a Service claim traffic to any address on every node, with no ownership check. Nothing here uses it: LoadBalancer addresses come from Cilium's pools. `NodeRestriction` is repeated because the flag replaces kubeadm's default list |
| `--tls-cipher-suites` and `--tls-min-version` | Go's defaults still include CBC and 3DES suites; the list keeps AEAD suites on ECDHE, which every client here speaks (kubectl, the controllers and the kubelets are all Go). TLS 1.3 ignores the list; the floor stops a downgrade to 1.0 or 1.1 |
| `protectKernelDefaults: true` | The kubelet no longer rewrites `vm.overcommit_memory`, `vm.panic_on_oom`, `kernel.panic` and `kernel.panic_on_oops` at start and refuses to start unless they are already right. `/etc/sysctl.d/k8s.conf` in the node config sets them, and the two travel together: without the drop-in a fresh node comes up at the kernel defaults, the kubelet exits and the node never joins, which shows at the next `make reinstall`, not on merge |
| `kernel.panic` with `kernel.panic_on_oops` | An oops reboots the node after a few seconds instead of leaving it wedged, so Ceph and etcd start recovering without a hand power-cycle |
| `podPidsLimit` | Bounds the PIDs one pod can hold, so a process that stops reaping its children cannot exhaust the node's PID space and take the kubelet with it. Well above anything running here, well below `kernel.pid_max` |
| `systemReserved`, `kubeReserved` and `evictionHard` memory | The reservations cap `kubepods.slice` below the node's RAM, so a pod that grows, the API server included, is OOM-killed inside its own cgroup instead of starving systemd-networkd, sshd and the kubelet. The eviction threshold makes the kubelet evict before the kernel has to act. A running node needs the same values in the `kubelet-config` ConfigMap, or `kubeadm upgrade node` reverts them |
| `/etc/tmpfiles.d/kubernetes-cis.conf` | kubeadm writes its files 0644 and Cilium creates the CNI directory 0755; CIS wants 0600 and 0700. systemd-tmpfiles applies the modes at boot and a kubelet `ExecStartPre` re-applies them before every start, when kubeadm has just rewritten them. See [Triage 2026-09-20](../operations/triage-2026-09-20.md#infra-the-nodes) |
| `systemd-sysupdate-reboot.timer` masked | Flatcar ships a vendor symlink that runs it despite a disabled preset. It would reboot on its own schedule, behind [Kured](../platform/kured.md) and without a drain, and it fails every run anyway: it acts on the default sysupdate component, which holds only `noop.conf` |
| `noop.conf` in `/etc/sysupdate.d/` | The unsuffixed directory is the default component that `systemd-sysupdate.service` acts on; the no-op transfer keeps that run from failing while the real transfer sits in the `kubernetes.d` component |

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
| Key | 32 random letters and digits (about 190 bits), generated once by `make config` into `output/credentials/encryption_key` and base64-encoded into the config as the `secretbox` key |
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
namespace (`authentik` excepted, where an admission policy narrows it to the
workload's own blueprint ConfigMap), and at cluster scope may create nothing
but namespaces, never at the `privileged` Pod Security level — see
[Security Policies](../platform/security-policies.md#admission-policies).

**Every platform namespace is default-deny in both directions**, one
`CiliumNetworkPolicy` file per namespace plus the cluster-wide rules, and
Cilium enforces them: `policyAuditMode` is off, so a flow no rule allows is
dropped — see
[Security Policies](../platform/security-policies.md#network-policies) and
[Rollout](../platform/security-policies.md#rollout).

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
to either Gateway and claim a hostname inside its listener's pattern — see
[Gateway API](../platform/gateway-api.md#configuration).

## Exposed interfaces

Every platform UI on the infra gateway, and Flowscape and the Extractor admin
UI on the apps gateway, is behind
[Authentik](../platform/authentik.md), and only accounts in `infra-users` get
past it. The wollbi editors also have Authentik accounts, so being logged in
is not enough. The last two rows are not behind Authentik:

| Service | Authentication | Group required |
| --- | --- | --- |
| ArgoCD | Authentik OIDC; local admin disabled. The server runs with `--insecure` because TLS terminates at the Gateway | `infra-users`, then `argocd-admins` or `argocd-viewers` for any access |
| Grafana | Authentik OIDC; login form disabled | `infra-users`; refused without `grafana-admins`, `grafana-editors` or direct `infra-users` |
| Hubble UI | Authentik proxy outpost | `infra-users` |
| Rook dashboard | Authentik proxy outpost | `infra-users` |
| Prometheus | Authentik proxy outpost | `infra-users` |
| Alertmanager | Authentik proxy outpost | `infra-users` |
| Flowscape, Extractor admin UI | Authentik proxy outpost | `infra-users` |
| OpenBao UI | Token or configured auth method — not behind Authentik | — |
| Agent SSH, `*.ssh.wlkr.ch` | The `ssh` TLS-passthrough listener on `apps-gateway`, which external-dns publishes on the public address. Nothing in front authenticates it: `sshd` in each agent pod admits the user `agent` with public keys only — see [SSH listener](../platform/gateway-api.md#ssh-listener) and the workloads repository's [Claude agents](https://github.com/JanWelker/homelab-apps/blob/main/docs/claude-agents.md) page | — |

The groups and how they nest are in
[Groups and roles](../platform/authentik.md#groups-and-roles).

Authentik is therefore a dependency of reaching any of them; the break-glass
paths are in [When Authentik is down](../platform/authentik.md#when-authentik-is-down).
OpenBao is left out deliberately: it holds Authentik's own database password,
so putting it behind Authentik would be a loop.

## What would tighten this up

Roughly in order of value against effort:

1. Narrow `sourceRepos` on the AppProjects to this repository and the Helm
   repositories actually in use.
2. Restrict `allowedRoutes` on `infra-gateway` to the platform namespaces, and
   promote the registry allowlist to `Deny`.
3. Add a second Alertmanager receiver on a different transport, so a failure of
   the mail path is not itself unmonitored.
4. Move etcd encryption to a KMS provider, removing the static
   `encryption_key` in `output/credentials/`.
5. Get the backups out of the cluster: Velero and the etcd snapshots write to
   the Ceph object store they are backing up.

See [Known Limitations](limitations.md) for the operational counterparts.
