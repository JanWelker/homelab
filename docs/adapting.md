---
description: "What to change to run this project against your own hardware, domain, and Git repository."
---

# Adapting This for Your Cluster

This repository documents one homelab, built from the pieces in
[Concepts](concepts.md), with its node names, addresses, domain and Git URL
hardcoded in `payload/` and `ansible/`. Make the changes below in your fork
before step 1 of the [Quickstart](quickstart.md), and push them: ArgoCD reads
Git, not your working tree.

## 1. Fork and repoint ArgoCD

Every ArgoCD `Application` points at this repository; until you change them,
your own commits have no effect. The match ends in `.git`, so the workloads
repository is left alone:

```bash
git grep -lz 'github.com/JanWelker/homelab.git' -- payload/ | \
  xargs -0 perl -pi -e 's|github\.com/JanWelker/homelab\.git|github.com/YOUR_USER/YOUR_REPO.git|g'
```

The workloads repository is a second URL, in
`payload/platform/argocd-projects/projects.yaml` (the `apps` project's
`sourceRepos`) and `payload/workloads/applicationset.yaml`. Point both at your
fork of [homelab-apps](https://github.com/JanWelker/homelab-apps), and change
the `repoURL` in every `*/application.yaml` of that fork too, or your cluster
deploys the author's applications. Delete the application directories you
will not run, with their prompts in `scripts/bao-secrets.sh`; what the
platform names per workload is in
[GitOps Strategy](architecture/gitops.md#workloads-live-in-a-second-repository).

A private fork also needs
[repository credentials](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/#repositories)
in ArgoCD.

## 2. Choose your domain

Four hostname patterns carry traffic, each a listener in
`gateway-api/gateways.yaml`, a zone in external-dns's `domainFilters` and,
for HTTPS, a wildcard `Certificate` in `certificates/certificates.yaml`, all
under `payload/platform/`:

| Pattern | Purpose | In a fork |
| --- | --- | --- |
| `*.k8s.<your-domain>` | user-facing workloads, via `apps-gateway` | rename |
| `*.infra.k8s.<your-domain>` | platform UIs (ArgoCD, Grafana, Hubble, OpenBao, Rook), via `infra-gateway` | rename |
| `*.ssh.<your-domain>` | the [SSH listener](platform/gateway-api.md#ssh-listener) for in-cluster agent sessions | rename, or delete the listener and the zone |
| `*.wollbi.ch` | a second site family on `apps-gateway`, with its own listener and certificate | delete `payload/platform/authentik/secrets-*-wollbi.yaml`, and the `wollbi` entries in the other files `git grep -l wollbi -- payload/` lists |

Replace the first three in one pass. It rewrites hostnames only, so label and
annotation keys such as `homelab.wlkr.ch/backup-exempt` and
`host-firewall.wlkr.ch/exempt` keep the names the workloads and the
[lockout runbook](platform/security-policies.md#host-firewall-lockout) use:

```bash
git grep -lzE '(k8s|ssh)\.wlkr\.ch' -- payload/ | \
  xargs -0 perl -pi -e 's/\b(k8s|ssh)\.wlkr\.ch\b/$1.YOUR-DOMAIN.example/g'
```

You must own the domain: Let's Encrypt's DNS-01 challenge needs write access
to its zone, so `.local`, `.lan` and made-up TLDs fail.

## 3. Point DNS at the gateway IPs

Each Gateway asks for a fixed address in its `spec.addresses`, which must lie
inside the Cilium pools in `payload/platform/cilium/lb-pools.yaml`, or the
Gateway is never programmed:

| Gateway | DNS record |
| --- | --- |
| `apps-gateway` | `*.k8s.<your-domain>` |
| `infra-gateway` | `*.infra.k8s.<your-domain>` |

Change both pools and both `spec.addresses` to free LAN addresses outside the
DHCP range, so the router never hands one out, then create the two wildcard
`A` records. `apps-gateway` also carries an
`external-dns.kubernetes.io/target` annotation, the public address
external-dns publishes for its names: set your own, or delete it to publish
the LAN address — see [external-dns](platform/external-dns.md#configuration)
and [Gateway API](platform/gateway-api.md#configuration). Two more things
decide whether anything answers on those addresses:

- The `CiliumL2AnnouncementPolicy` in `lb-pools.yaml` answers ARP for them
  from the control planes only, on interfaces matching a pattern: change its
  `nodeSelector` and `interfaces` to fit. L2 needs the addresses in the
  nodes' subnet.
- `payload/platform/cilium/bgp.yaml` also announces them to the router over
  eBGP, and its peer and ASNs must match the inventory (step 5). With no BGP
  router, delete it and `bgp-secret.yaml` beside it, and skip the
  `cilium/bgp` path in `scripts/bao-secrets.sh` as in
  [Quickstart step 11](quickstart.md#setup).

## 4. Set up the DNS-01 solver

`payload/platform/certificates/cluster-issuers.yaml` is written for **AWS
Route53**. Update:

- `email:` — your address, on both issuers. Let's Encrypt sends expiry notices here.
- `region:` — the Route53 region.

DNS hosted elsewhere means more than the matching
[cert-manager DNS-01 provider](https://cert-manager.io/docs/configuration/acme/dns01/):
external-dns is configured for AWS too, and the egress policies of both admit
only that API. `git grep -il route53 -- payload/ scripts/` lists every file,
the credential prompts included.

!!! tip
    Switch `issuerRef` in `certificates.yaml` to `letsencrypt-staging` while iterating: production allows 5 duplicate certificates per week, and a broken solver burns through that in minutes.

## 5. Describe your hardware

Edit `ansible/inventory.yaml`; every variable is listed under
[Quickstart &rarr; step 2](quickstart.md#setup). The ones a fork must choose
rather than copy, and the payload settings that repeat them:

| Setting | Notes |
| --- | --- |
| `control_plane_vip` | Free address on the nodes' subnet, outside DHCP and distinct from the LoadBalancer pools. Becomes the API endpoint in the cluster certificates. |
| `control_plane_vip_interface` | The NIC kube-vip advertises on; before provisioning, `ip -br link` on any live Linux booted on the machine usually shows the name Flatcar gives it. |
| `control_plane_endpoint_host` | Set a name on a fresh build, see [Endpoint name](operations/control-plane-vip.md#endpoint-name); a running cluster keeps the VIP. |
| host entries | Each needs `ansible_host`, `mac_address` (the NIC that PXE boots) and a matching DHCP reservation — see [Quickstart &rarr; Other requirements](quickstart.md#other-requirements). |
| `router_ip`, `router_asn`, `cluster_asn` | `payload/platform/cilium/bgp.yaml` must name the same values, since the payload cannot read the inventory — see [Cilium](platform/cilium.md#configuration). |
| `providerRegex`, `providerIpPrefixes` in `payload/platform/kubelet-csr-approver/application.yaml` | Your node names and subnet, or every kubelet serving certificate is denied — see [metrics-server](platform/metrics-server.md#verifying-the-kubelet-instead-of-trusting-it). |

Then update `payload/platform/cilium/values.yaml`:

- `k8sServiceHost` — `control_plane_vip`, from the start on a new build,
  where the bootstrap static pod holds it; a literal, because Cilium replaces
  `kube-proxy` and cannot reach the API through a Service — see
  [Design Decisions &rarr; Cilium](architecture/decisions.md#cilium-as-cni-replacing-kube-proxy).
- `devices` — the interface prefix Cilium binds to, found the same way.

And `payload/platform/kube-vip/daemonset.yaml`, which cannot read the
inventory either:

- `address` — the same value as `control_plane_vip`.
- `vip_interface` — the same value as `control_plane_vip_interface`.

A mismatch fails only at the first sync, when the DaemonSet replaces the
static pods and advertises somewhere else.

Two more places carry the author's network and mail:

- `git grep -nE '10\.[0-9]+\.[0-9]+\.[0-9]+' -- payload/` lists the address
  literals, among them the etcd peers in the
  [host firewall](platform/security-policies.md#host-firewall) and the
  subnets Tetragon's tracing policies treat as internal.
- The mail relay is `smtp_smarthost` in
  `payload/platform/monitoring/alertmanager-config.yaml`, and its name a
  `toFQDNs` entry in `network-policies/monitoring.yaml` — see
  [Alerting](platform/monitoring.md#alerting).

## 6. Provisioning access

The SSH key in [Quickstart &rarr; Other requirements](quickstart.md#other-requirements)
is the only way into the nodes: there is no password, no console login and no
rescue path short of reprovisioning.

## 7. The documentation site

To publish your own copy, set `site_url`, `repo_url`, `repo_name` and
`copyright` in `zensical.toml` and your domain in `docs/CNAME` (delete it for
none); `docs.yaml` then publishes to your GitHub Pages. Otherwise delete
`.github/workflows/docs.yaml`.

## Parts tied to the author's accounts

| Component | Why | Read |
| --- | --- | --- |
| `arc-controller`, `arc-runners` | `githubConfigUrl` names a repository of the author's, and the runners register with a GitHub App installed there | [GitHub runners](platform/github-runners.md#github-app) |
| `apps` project in `argocd-projects` | `sourceRepos` lists the author's chart registries | [ArgoCD &rarr; Projects](platform/argocd.md#projects) |
| Claude agents | `security/claude-agent-rbac.yaml`, the SSH listener and an Argo CD account serve the author's sessions | [Agent RBAC](platform/security-policies.md#agent-rbac) |
| Repository settings | `.github/CODEOWNERS`, `reviewers` and the preset in `renovate.json`, and `ci-ok.yaml` name the author's account | [Maintenance](development/maintenance.md) |

To drop a component, delete its directory under `payload/platform/`, the
lines `git grep -n <namespace> -- payload/` lists for each of its namespaces,
and its path in `scripts/bao-secrets.sh` as in
[Quickstart step 11](quickstart.md#setup).
