---
description: "What the Trivy Operator findings actually amount to, which ones are fixed here, which wait on an upstream release, and which are never going to move."
---

# Vulnerability Triage

Trivy Operator writes one `VulnerabilityReport` per container, and on a
cluster this size that is a few thousand findings. Almost all of them warrant
no action, and the work is in proving that quickly rather than in reading them.
This page is the result of one such pass, on 2026-09-20, kept so the next one
starts from the disposition rather than from the raw count.

The operator itself is a workload, so how it is configured and how to read its
reports lives with it, in the
[homelab-apps documentation](https://janwelker.github.io/homelab-apps/trivy-operator/).
This page is about what the findings mean for *this* platform.

## How to read a report

Three things before believing a number:

- **A missing report is not a clean image.** A scan that failed writes nothing.
  Reconcile running images against scanned ones before concluding anything;
  the `TrivyContainerNotScanned` alert does it continuously. On this pass it
  named Nextcloud and Authentik, whose scans were dying on a five-minute Job
  deadline that a longer Trivy timeout could never reach, and whose reports,
  once a scan finished, were too large for etcd: 2886 Debian findings with no
  fix in one image. Both are handled in homelab-apps (#26, #28); the second
  with a per-namespace policy that drops only unfixed findings below HIGH.
- **An empty `fixedVersion` means no fix is known, not no problem.** They are
  kept on purpose (`ignoreUnfixed: false`), because a cluster of them in one
  base image is the signal that the base image is the wrong one.
- **The worst findings are usually not the project's code.** The report's
  `target` names the binary. Argo CD's criticals are in the `kustomize` and
  `git-lfs` it bundles; dex's are in `gomplate`; Rook's are in `s5cmd`.
  Which binary decides whether anything can be done, and by whom.

The pull, and the query that separates fixable from not:

```bash
kubectl get vulnerabilityreports -A -o json > vr.json
jq -r '.items[] | .metadata.namespace as $ns
  | .report.artifact.repository as $img
  | .report.vulnerabilities[]?
  | select(.severity=="CRITICAL")
  | [$ns, $img, .vulnerabilityID, .resource, .installedVersion,
     (.fixedVersion // "NO-FIX"), (.packagePath // .target)]
  | @tsv' vr.json | sort -u
```

## Where the findings stand

Every image with a critical finding, or a high one with a fix, and what was
decided about it. "Waiting on release" means the fix is already merged upstream
and nothing here can speed it up; Renovate takes the release when it exists.

| Image | Finding | Disposition |
| --- | --- | --- |
| CSI sidecars (`csi-provisioner`, `csi-resizer`, `csi-snapshotter`) | gRPC-Go authorization bypass, CVE-2026-33186 | **Fixed here.** Pinned to the versions ceph-csi-operator v1.0.5 defaults to; Rook still ships v1.0.4. See [Rook-Ceph &rarr; CSI driver](../platform/rook-ceph.md#csi-driver). |
| `argoproj/argocd` v3.5.3 | Go stdlib and `x/net` in bundled `kustomize` (built with Go 1.24.0) and `git-lfs` 3.7.1; `grpc`, `oras-go` in argocd itself | **Waiting on release.** `master` has git-lfs 3.8.0, grpc 1.83.2 and oras-go 2.6.2. kustomize 5.8.1 is the latest kustomize release; only a new kustomize build fixes that one. Argo CD's `SECURITY.md` asks not to file scanner findings. |
| `dexidp/dex` v2.45.1 | OpenSSL in Alpine 3.23, `grpc`, `goxmldsig`, stdlib; a second set in bundled `gomplate` | **Waiting on release.** Everything is fixed on `master`, nothing has been released since March 2026. Tracked in [dexidp/dex#4948](https://github.com/dexidp/dex/issues/4948), with this cluster's scan data added on 2026-09-18. |
| `rook/ceph` v1.20.7 | Go stdlib in bundled `s5cmd` 2.3.0 (built with Go 1.22.10) | **Not reachable, waiting on release.** Nothing in Rook's Go code invokes `s5cmd`; it is a CLI for the toolbox. s5cmd 2.3.0 is its latest release; [peak/s5cmd#873](https://github.com/peak/s5cmd/issues/873) and [#820](https://github.com/peak/s5cmd/issues/820) ask for a new one. |
| `home-assistant/home-assistant` | OpenSSH in Alpine 3.24.1, Go stdlib in bundled `tempio` (Go 1.23.3) and `go2rtc`, Python `anyio` | **Waiting on release.** Home Assistant's base image already carries tempio 2026.07.0; core is on an older base. Weekly releases. The Open Home Foundation forbids autonomous agents from filing anything, so nothing is filed from here. |
| `library/postgres` 17.11 (Authentik) | Go stdlib in `gosu` 1.19; `libxml2` in Debian 13 with no fix | **Not reachable, nothing to do.** The stdlib CVE is TLS session resumption and gosu opens no connections. 17.11 is the latest 17.x; libxml2 waits on Debian. |
| `cloudnative-pg/postgresql` 18.6 | 31 highs and one critical, none with a fix (Debian 13) | **Nothing to do.** The base is current; every finding waits on Debian. |
| `coredns/coredns` v1.14.6 | Two highs fixed in 1.14.7: memory exhaustion and UPDATE forwarding, both on DoH, DoQ and gRPC listeners | **Not reachable.** kubeadm's Corefile serves plain UDP and TCP only. kubeadm 1.37 pins 1.14.6; it moves when kubeadm does. |
| `grafana/grafana` | `grpc`, `otel`, `x/net` in thirteen bundled datasource plugin binaries | **Waiting on release.** The plugin binaries are rebuilt by Grafana's own release, and 13.2.2 is the current one. Routine churn in a bundled binary, not a report. |
| `ceph/ceph` v20.2.4 | `setuptools` 69.2 under Python 3.9 | **Not reachable.** The CVEs are in `easy_install` and `package_index`, which no Ceph daemon calls. |
| `openbao/openbao` 2.6.2 | `github.com/openbao/openbao` "fixed in 2.5.4" | **Scanner artifact.** The binary carries a Go pseudo-version, which sorts below every real tag. |
| Everything else | `google.golang.org/grpc` one or two patches behind, Go stdlib one patch behind | **Routine churn.** Fixed upstream in late August; every project here has a bot that takes the next release. Not a finding. |

The three exposed-secret findings are the same one: `ssl-cert-snakeoil.key`,
the placeholder Debian's `ssl-cert` package generates in every Postgres image.
It is not a secret.

## What is not filed, and why

Filing goes out under a real name, permanently, and most projects have said
what they want. Checked on this pass:

- **Argo CD** asks in `SECURITY.md` not to raise issues found by a scanner,
  and its own Renovate has already moved the bundled tools on `master`.
- **Home Assistant** (Open Home Foundation) does not allow autonomous agents
  to open issues or pull requests and closes them on sight.
- **Rook** allows AI-assisted contributions with disclosure but requires a
  human to submit them. The one thing worth asking Rook for, bumping to
  ceph-csi-operator v1.0.5 so the sidecar pins here can go, is drafted for a
  person to post.
- **Routine Go churn is not a finding.** A dozen "please bump grpc" issues
  damage standing for the one time something real turns up.

## The other three report kinds

`ConfigAuditReport`, `RbacAssessmentReport` and `InfraAssessmentReport` are
fixable here rather than upstream, which makes them worth more per finding and
also makes it worth being honest about which ones are load-bearing.

### Infra: the nodes

The node collector runs CIS's file checks on every node. Real modes, read on
a control-plane and a worker node:

| Check | Path it stats | Was | Disposition |
| --- | --- | --- | --- |
| KCV-0077 kubelet config | `/var/lib/kubelet/config.yaml` | 644 | **Fixed**, 600 |
| KCV-0075 CA file | `/etc/kubernetes/pki/ca.crt` | 644 | **Fixed**, 600 |
| KCV-0056 CNI files | `/*/cni/*`, so `/opt/cni/bin` | 755 | **Fixed**, 700 |
| KCV-0069 kubelet unit | `/lib/systemd/system/kubelet.service` | 644 | **Accepted.** The unit ships in the Kubernetes sysext, on a read-only, signed `/usr`. It holds no secret. |
| KCV-0059 etcd data dir | `/var/lib/etcd/default.etcd` | absent | **Artifact.** kubeadm uses `/var/lib/etcd`, which is already `700 etcd:etcd`. |
| KCV-0001 and the other API server flags | static pod args | | **Tracked in #660.** Anonymous auth in particular cannot simply be switched off, because the kubelet's probes rely on it; see #678 for the shape that works. |

kubeadm writes the first three itself, at 644, on every `init`, `join` and
`upgrade`, so a one-off `chmod` would not survive. `butane_node_config.yaml.j2`
instead drops `/etc/tmpfiles.d/kubernetes-cis.conf` with three `z` lines and
has the kubelet's drop-in run `systemd-tmpfiles --create` on that file before
every start. Boot applies it; a kubelet restart, which kubeadm triggers right
after rewriting the files, applies it again. A path that does not exist yet is
skipped, so the first boot is unaffected. The change is provisioning-time: a
node picks it up on its next rebuild.

### Config audit: the pods

Two checks are most of the volume. `KSV-0014` wants a read-only root
filesystem and `KSV-0118` fires when the *pod-level* `securityContext` is
empty, whatever the containers set. Where they land:

| Where | Disposition |
| --- | --- |
| Cilium, cilium-envoy, kube-vip, the kubeadm static pods, Rook OSDs and mons, node-exporter, Kured | **Load-bearing.** Host network, host PID, privileged and the added capabilities are what these do. kube-vip in particular is left exactly as the static pod it replaced: untested hardening there drops the API VIP. `pod-security.yaml` already enforces `privileged` in those namespaces for this reason. |
| `etcd-backup` CronJob | **Own manifest, hardened.** Seccomp, no capabilities, no privilege escalation, read-only root. Host network stays, because etcd listens on the node's loopback, and root stays, because the client certificates are `600 root`. |
| Argo CD, External Secrets, Trivy Operator, metrics-server, kubelet-csr-approver, snapshot-controller, Alloy | **`KSV-0118` only, fixed.** Each container already ran non-root with capabilities dropped; the pod-level context was what was empty, and each chart has a value for it (#791, and homelab-apps#27 for Trivy Operator). kured's chart has no pod-level value and is privileged regardless; CoreDNS is kubeadm's. |
| Authentik, Nextcloud, Home Assistant, Grafana's sidecars, Velero, OpenBao | **Read-only root not attempted.** Each writes somewhere under `/` at runtime; the chart or image decides where, and guessing costs an outage. Also #663. |

### RBAC

Every critical and high is a `ClusterRole` a chart ships for its operator:
cert-manager, CloudNativePG, Rook, External Secrets, the Prometheus operator,
Loki, Alloy and Trivy Operator all manage Secrets because that is their job,
and `argocd-application-controller` manages everything because it deploys
everything. The rest are Kubernetes' own `admin`, `edit` and `cluster-admin`.
Nothing here is narrowable without forking a chart, and the exposure is what
[Security Posture &rarr; Authorization](../architecture/security.md#authorization)
already describes.
