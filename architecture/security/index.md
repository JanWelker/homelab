# Security Posture

This is a homelab on a private network, and several deliberate shortcuts follow from that. They are listed so a reader can tell a decision from an oversight.

## Assumed trust boundary

The cluster assumes a **trusted L2 network segment**: anything with a port on it is treated as friendly. There is no VPN requirement and no mutual TLS between components; inside the cluster, the default-deny network policies in [Authorization](<#authorization>) are the segmentation.

The published hostnames are a partial exception. Certificates come from Let's Encrypt through a DNS-01 challenge against a public zone, so `argo.infra.k8s.wlkr.ch` and its siblings are publicly resolvable and appear in Certificate Transparency logs, though they point at RFC1918 addresses.

## Provisioning

Provisioning is the least protected phase: it runs before any of the cluster's own security exists.

| Property | Detail |
| --- | --- |
| Ignition configs travel over plain HTTP | The boot server gives `ignition-<host>.json` only to that host's `ansible_host`, and the installer config pins its sha512. A LAN host that forges the address or sits in between still reads it |
| Those configs embed join credentials | The bootstrap `token`, and on control-plane nodes the `certificateKey`, together enough to join a new control-plane node. Only the init host has them in its kubeadm config; the others get a `0600` environment file that `bootstrap-k8s.service` names by variable. The service deletes both files once the node has joined, so neither `systemctl show` nor the disk keeps them |
| The control-plane configs embed the etcd encryption key | `/etc/kubernetes/enc/encryption-config.yaml` is inlined with the key that encrypts every Secret at rest. It has no TTL: whoever fetched a control-plane config can read Secrets out of any etcd backup for the life of the cluster |
| Nodes join with `--discovery-token-unsafe-skip-ca-verification` | A joining node does not verify the API server's CA |
| Sysext updates set `Verify=false` | On first boot Ignition checks each image against the sha256 the boot server was given, so a substituted image fails. Later sysupdate downloads travel over HTTPS with signature verification off; the transfer config naming their source is inlined from Git, not fetched |
| The PXE kernel, initrd and bootloader are signature-checked | `make download` takes their sums only from a clearsigned file that `gpgv` verifies against a pinned key fingerprint: Flatcar's image signing key for `.DIGESTS.asc`, kernel.org's autosigner for syslinux's `sha256sums.asc` |
| The OS image is signature-checked | `flatcar-install` is given `-b`/`-V`, so the node downloads the image and its detached signature and checks both against Flatcar's key before writing anything |

The mitigation is time: `make serve` is a foreground command, the bootstrap token and the certificate key expire (the windows are in [Switching back to local boot](<https://homelab.wlkr.ch/architecture/boot-process/index.md#switching-back-to-local-boot>)), and `make config` re-arms both only for those windows, over SSH through a script's stdin, so no process list shows them. The encryption key has no clock, which is why HTTP listens only on `boot_server_ip`, serves files by name and lists nothing. TFTP listens on every interface, since macOS lets an unprivileged process bind port 69 only there, and refuses every write request, so nobody can plant a boot menu. **Stop the boot server when provisioning is finished.** Flatcar is installed to disk and nothing needs it after a build — see [Every boot after the first](<https://homelab.wlkr.ch/architecture/boot-process/index.md#every-boot-after-the-first>).

`output/credentials/` holds the generated bootstrap token and certificate key in plaintext. The directory is `0700`, the Ignition configs in `output/http` are `0600`, and `output/` is gitignored. The values are reused across `make config` runs, because a node whose join window passed retries with the values on its disk once `make config` has re-armed them ([Adding a node](<https://homelab.wlkr.ch/operations/nodes/index.md#adding-a-node>)). Delete them to force new ones. Each node's SSH host key is generated there too, installed by Ignition and listed in `output/known_hosts`, which the playbooks' SSH options trust alone (`StrictHostKeyChecking=yes`); a rebuilt node keeps its key and nothing is trusted on first contact. The private halves are as sensitive as the token. The installer keeps its copy of the node config `0600` in RAM; `flatcar-install` carries that mode to the OEM partition.

## Node hardening

`ansible/templates/kubeadm.yaml.j2` and `ansible/templates/butane_node_config.yaml.j2` carry the settings below. They land at provisioning time and change only on `make reinstall`.

| Setting | Why |
| --- | --- |
| `proxy.disabled: true` | Cilium replaces `kube-proxy`. `kubeadm upgrade apply` reads the field from the `kubeadm-config` ConfigMap; a `skipPhases` on init leaves `proxy: {}` stored, and every upgrade restores `kube-proxy` |
| `--kubelet-certificate-authority` | The API server verifies the kubelet's serving certificate for exec, logs, attach and port-forward, the calls that carry command output. `serverTLSBootstrap` has the kubelet request its certificate from the cluster CA, and [kubelet-csr-approver](<https://homelab.wlkr.ch/platform/metrics-server/index.md>) approves it |
| `--admission-control-config-file` | A [Pod Security default](<https://homelab.wlkr.ch/platform/security-policies/index.md#pod-security-admission>) for namespaces without labels, so a namespace nobody labelled is `baseline`, not `privileged` |
| `--authentication-config` | Anonymous requests reach only `/livez`, `/readyz`, `/healthz` (the kubelet's probes, `make reinstall`) and `kube-public/cluster-info`, which `kubeadm join` reads before it holds a credential. The file replaces `--anonymous-auth`, which must stay unset |
| `--profiling=false` on the API server, controller-manager and scheduler | `/debug/pprof` exposes internals and helps nothing here; CIS asks for it off |
| Metrics listeners on the node address | etcd's plain-HTTP metrics and the controller-manager and scheduler bind the node's own address, where Prometheus scrapes them, instead of every interface. The address differs per node, so it comes from per-node JSON patches in `/etc/kubernetes/patches` rather than the cluster-wide `ClusterConfiguration`; the patches also move the probes, and etcd keeps `127.0.0.1` for its own |
| `DenyServiceExternalIPs` | `spec.externalIPs` lets whoever can create a Service claim traffic to any node address, unchecked. Nothing here uses it. `NodeRestriction` is repeated because the flag replaces kubeadm's default list |
| `--tls-cipher-suites` and `--tls-min-version`, `tlsCipherSuites` and `tlsMinVersion` | Go's defaults still include CBC and 3DES suites; the list keeps ECDHE AEAD suites, which every Go client here speaks. TLS 1.3 ignores the list; the floor stops a downgrade to 1.0 or 1.1. The kubelet's listener on 10250 gets the same |
| `shutdownGracePeriod` and `shutdownGracePeriodCriticalPods` | A `systemctl reboot` without the [documented drain](<https://homelab.wlkr.ch/operations/nodes/index.md#rebooting-a-node>), or an ACPI power-off, waits while pods get their SIGTERM window instead of dying mid-write |
| `protectKernelDefaults: true` | The kubelet refuses to start unless `vm.overcommit_memory`, `vm.panic_on_oom`, `kernel.panic` and `kernel.panic_on_oops` are already right. `/etc/sysctl.d/k8s.conf` sets them; without the drop-in a fresh node never joins |
| `kernel.panic` with `kernel.panic_on_oops` | An oops reboots the node within seconds instead of wedging it, so Ceph and etcd recover without a power-cycle |
| `seccompDefault: true` | A pod without a seccomp profile runs `RuntimeDefault`, not unconfined; a privileged pod stays unconfined; a running node needs it in the `kubelet-config` ConfigMap too |
| `kernel.dmesg_restrict` and `kernel.kexec_load_disabled` | An escaped container cannot read the kernel log or load another kernel; [Kured](<https://homelab.wlkr.ch/platform/kured/index.md>) reboots with `systemctl reboot`, not kexec |
| `/etc/audit/rules.d/` | SELinux denials reach the journal and [Loki](<https://homelab.wlkr.ch/platform/logging/index.md>). Flatcar's own rules exclude them: the exclude filter drops whatever a rule matches, even a `never` rule |
| `/etc/ssh/sshd_config.d/10-hardening.conf` | `AllowUsers core`, no keyboard-interactive, no `ssh-rsa`. Flatcar's drop-in appends `ssh-rsa` and sshd keeps a keyword's first value, so this file sorts ahead of it. `core`'s key is `~/.ssh/id_ed25519.pub` |
| `podPidsLimit` | Bounds the PIDs one pod can hold, so a process that stops reaping its children cannot exhaust the node's PID space and take the kubelet with it. |
| `systemReserved`, `kubeReserved` and `evictionHard` memory | The reservations cap `kubepods.slice` below the node's RAM, so a growing pod is OOM-killed in its own cgroup instead of starving systemd-networkd, sshd and the kubelet. A running node needs the same values in the `kubelet-config` ConfigMap, or `kubeadm upgrade node` reverts them |
| `/etc/tmpfiles.d/kubernetes-cis.conf` | kubeadm writes its files 0644 and Cilium creates the CNI directory 0755; CIS wants 0600 and 0700. systemd-tmpfiles applies them at boot and a kubelet `ExecStartPre` before every start. See [Triage 2026-09-20](<https://homelab.wlkr.ch/operations/triage-2026-09-20/index.md#infra-the-nodes>) |
| `systemd-sysupdate-reboot.timer` masked | Flatcar's vendor symlink runs it despite a disabled preset. It would reboot behind [Kured](<https://homelab.wlkr.ch/platform/kured/index.md>) without a drain, and it fails anyway: the default sysupdate component holds only `noop.conf` |
| `noop.conf` in `/etc/sysupdate.d/` | The unsuffixed directory is the default component that `systemd-sysupdate.service` acts on; the no-op transfer keeps that run from failing while the real transfer sits in the `kubernetes.d` component |

## Secrets

Secrets live in [OpenBao](<https://homelab.wlkr.ch/platform/openbao/index.md>) and reach workloads as native Kubernetes `Secret` objects through the [External Secrets Operator](<https://homelab.wlkr.ch/platform/external-secrets/index.md>). Nothing sensitive is committed to Git.

- OpenBao is sealed with Shamir and [unsealed by hand](<https://homelab.wlkr.ch/platform/openbao/index.md#unsealing-after-a-restart>), so the 5 key shares are the root of trust for every other secret; losing all of them loses everything, and nothing outside the cluster holds a copy.
- No root token is kept: `make bao-init` revokes it once it has created the [operator login](<https://homelab.wlkr.ch/platform/openbao/index.md#operator-credential>), and one is generated from the shares when needed. The operator login reads and writes every KV path, so `output/credentials/` is as sensitive as before, but it cannot change policies or auth methods.
- A workload's namespace reaches only `kv/<namespace>/` in OpenBao; the platform's store refuses every other namespace — see [Kubernetes auth](<https://homelab.wlkr.ch/platform/openbao/index.md#kubernetes-auth>).
- A Kubernetes `Secret` is base64, not encryption. Anyone with `get secrets` in a namespace can read what ESO materialised there. Encryption at rest protects the bytes in etcd, not the API.
- Velero's resource backups hold every `Secret` as plain JSON under the `offsite` bucket's `backup.velero/` prefix, so its keys and RGW bypass encryption at rest; only the volume data is encrypted — see [Velero](<https://homelab.wlkr.ch/platform/velero/index.md#repository-password>).

### Encryption at rest

The API server encrypts `secrets` with `secretbox` before they reach etcd (`ansible/templates/kubeadm.yaml.j2`; the key file in `ansible/templates/butane_node_config.yaml.j2`). Without it an etcd backup or stolen disk yields every credential the cluster holds.

| Property | Detail |
| --- | --- |
| Provider | `secretbox`, with `identity` listed after it |
| Key | 32 random letters and digits (about 190 bits), generated once by `make config` into `output/credentials/encryption_key` and base64-encoded into the config as the `secretbox` key |
| Scope | `secrets` only; ConfigMaps and other resources are unencrypted |
| Distribution | The same key on every control-plane node, written by Ignition to `/etc/kubernetes/enc/encryption-config.yaml` (mode `0600`) |

With `identity` last, new writes are encrypted and Secrets written before it was enabled stay readable but are not rewritten. To encrypt what already exists, once the API servers have restarted:

```bash
kubectl get secrets -A -o json | kubectl replace -f -
```

The key lives in `output/credentials/` beside the kubeadm token and certificate key — that directory is what decrypts etcd. The API servers reload the file on change (`encryption-provider-config-automatic-reload`), so rotating it is a file edit and a rewrite of every Secret, by hand — see [Encryption Key Rotation](<https://homelab.wlkr.ch/operations/encryption-key-rotation/index.md>). A KMS provider would remove the key file, at the cost of a dependency the cluster must reach before it can serve Secrets.

## Runtime

[Tetragon](<https://homelab.wlkr.ch/platform/tetragon/index.md>) records every exec in every pod and alerts when anything but the control plane reads the cluster's key material. It is detection, not prevention: nothing is killed, and a policy that fails to load on a new kernel watches nothing until its alert fires.

## Audit logging

The API server records who did what, to which object, and whether it was allowed; the `audit` labels of Pod Security Admission need it. Policy, retention and queries are in [Audit Logging](<https://homelab.wlkr.ch/architecture/audit-logging/index.md>).

## Authorization

**AppProjects constrain the workloads, not the platform.** `payload/platform/argocd-projects/projects.yaml` defines them. `infra` allows `sourceRepos: "*"` and every group and kind in every namespace: it is this repository deploying this repository, so a restriction guards against nothing PR review does not. `system` is confined to the `argocd` namespace. `apps` has the boundary to draw, because its Applications come from a second repository with a lighter review: it accepts only the workloads repository and the chart repositories the workloads use, may not write into any platform namespace (`authentik` included, by an admission policy), and at cluster scope may create nothing but namespaces, never at the `privileged` Pod Security level — see [Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#admission-policies>).

**Every platform namespace is default-deny in both directions**, one `CiliumNetworkPolicy` file per namespace plus the cluster-wide rules, and Cilium enforces them: `policyAuditMode` is off, so a flow no rule allows is dropped — see [Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#network-policies>) and [Rollout](<https://homelab.wlkr.ch/platform/security-policies/index.md#rollout>).

**Pod Security Admission is on, but mostly auditing.** Every platform namespace carries `enforce` at the level it needs and `warn`/`audit` at a stricter one, so violations are visible without breaking what runs.

**Image references are checked at admission.** An untagged or `latest` image is refused, and so is a registry outside the allowlist. Signatures are not verified: few of the upstreams sign, so a verifier would exempt nearly everything. See [Security Policies](<https://homelab.wlkr.ch/platform/security-policies/index.md#admission-policies>).

**`apps-gateway` admits routes from every namespace** (`allowedRoutes.namespaces.from: All`), so a policy reserves the platform names — see [Route hostnames](<https://homelab.wlkr.ch/platform/gateway-api/index.md#route-hostnames>).

## Exposed interfaces

Every platform UI on the infra gateway is behind [Authentik](<https://homelab.wlkr.ch/platform/authentik/index.md>), and only accounts in `infra-users` get past it. The applications on the apps gateway, some of them public and without Authentik, are listed with their authentication on the [applications repository's pages](<https://homelab-apps.wlkr.ch/>). The last two rows are not behind Authentik:

| Service | Authentication | Group required |
| --- | --- | --- |
| ArgoCD | Authentik OIDC; local admin disabled. The server runs with `--insecure` because TLS terminates at the Gateway | `infra-users`, then `argocd-admins` or `argocd-viewers` for any access |
| Grafana | Authentik OIDC; login form disabled | `infra-users`; refused without `grafana-admins`, `grafana-editors` or direct `infra-users` |
| Hubble UI | Authentik proxy outpost | `infra-users` |
| Rook dashboard | Authentik proxy outpost | `infra-users` |
| Prometheus | Authentik proxy outpost | `infra-users` |
| Alertmanager | Authentik proxy outpost | `infra-users` |
| OpenBao UI | Token or configured auth method — not behind Authentik | — |
| Agent SSH, `*.ssh.wlkr.ch` | The `ssh` TLS-passthrough listener on `apps-gateway`, which external-dns publishes on the public address. Nothing in front authenticates it: `sshd` in each agent pod admits the user `agent` with public keys only — see [SSH listener](<https://homelab.wlkr.ch/platform/gateway-api/index.md#ssh-listener>) and the workloads repository's [Claude agents](<https://github.com/JanWelker/homelab-apps/blob/main/docs/claude-agents.md>) page | — |

The groups and how they nest are in [Groups and roles](<https://homelab.wlkr.ch/platform/authentik/index.md#groups-and-roles>).

Authentik is a dependency of reaching any of them; the break-glass paths are in [When Authentik is down](<https://homelab.wlkr.ch/platform/authentik/index.md#when-authentik-is-down>). OpenBao stays out: it holds Authentik's own database password, a loop.

## What would tighten this up

Roughly in order of value against effort:

1. Narrow `sourceRepos` on the AppProjects to this repository and the Helm repositories actually in use.
2. Add a second Alertmanager receiver on a different transport, so a failure of the mail path is not itself unmonitored.
3. Move etcd encryption to a KMS provider, removing the static `encryption_key` in `output/credentials/`.
4. Get a backup copy off the site: TrueNAS pulls the `offsite` bucket to the LAN, which survives losing the cluster but not the site.

See [Known Limitations](<https://homelab.wlkr.ch/architecture/limitations/index.md>) for the operational counterparts.
