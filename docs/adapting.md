---
description: "What to change to run this project against your own hardware, domain, and Git repository."
---

# Adapting This for Your Cluster

This repository documents one specific homelab. Node names, addresses, the
`wlkr.ch` domain, and the Git repository URL are hardcoded throughout
`payload/` and `ansible/`. Run the [Quickstart](quickstart.md) unchanged and you
get a cluster that syncs from *this* repository and requests certificates for a
domain you don't control.

Everything below is a change you make in your own fork, before step 1 of the
Quickstart.

## 1. Fork and repoint ArgoCD

Every ArgoCD `Application` points at this repository by URL. Until you change
them, your cluster pulls its desired state from here and your own commits have
no effect.

```bash
git grep -l 'github.com/JanWelker/homelab.git' -- payload/
```

Rewrite them to your fork. The match ends in `.git`, so the workloads
repository below is left alone:

```bash
git grep -lz 'github.com/JanWelker/homelab.git' -- payload/ | \
  xargs -0 sed -i '' 's|github.com/JanWelker/homelab.git|github.com/YOUR_USER/YOUR_REPO.git|g'
```

The workloads repository is a second URL, in
`payload/platform/argocd-projects/projects.yaml` (the `apps` project's
`sourceRepos`), `payload/workloads/applicationset.yaml` and
`payload/workloads/applicationset-previews.yaml`; the previews generator also
names the GitHub `owner` whose pull requests it lists. Point all four at your
fork of [homelab-apps](https://github.com/JanWelker/homelab-apps), or delete
the previews ApplicationSet if you have no sites to preview.

!!! note
    `sed -i ''` is the BSD/macOS form. On Linux use `sed -i` with no argument.

If your fork is private, ArgoCD also needs repository credentials — see the
[ArgoCD private repository docs](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/#repositories).

## 2. Choose your domain

Four hostname patterns carry traffic. Each is a listener in
`payload/platform/gateway-api/gateways.yaml` and a zone in external-dns's
`domainFilters` (`payload/platform/external-dns/application.yaml`); the HTTPS
ones also have a wildcard `Certificate` in
`payload/platform/certificates/certificates.yaml`:

| Pattern | Purpose | In a fork |
| --- | --- | --- |
| `*.k8s.<your-domain>` | user-facing workloads, via `apps-gateway` | rename |
| `*.infra.k8s.<your-domain>` | platform UIs (ArgoCD, Grafana, Hubble, OpenBao, Rook), via `infra-gateway` | rename |
| `*.ssh.<your-domain>` | the [SSH listener](platform/gateway-api.md#ssh-listener) for in-cluster agent sessions | rename, or delete the listener and the zone |
| `*.wollbi.ch` | a second site family on `apps-gateway`, with its own listener, certificate and PR previews | delete or rename every file `git grep -l wollbi -- payload/` lists |

Replace the first three in one pass; `wollbi.ch` does not match the pattern
and is handled by hand:

```bash
git grep -lz 'wlkr\.ch' -- payload/ | \
  xargs -0 sed -i '' 's|wlkr\.ch|YOUR-DOMAIN.example|g'
```

That covers the Gateway hostnames, the wildcard `Certificate` resources, every
`HTTPRoute`, ArgoCD's `global.domain`, and the
`link.argocd.argoproj.io/external-link` annotations.

You must own this domain: the certificates are issued by Let's Encrypt through
a DNS-01 challenge, which requires write access to the zone. `.local`, `.lan`
and made-up TLDs will not work.

## 3. Point DNS at the gateway IPs

The two Gateways take fixed addresses from the Cilium pools in
`payload/platform/cilium/lb-pools.yaml`:

| Gateway | DNS record |
| --- | --- |
| `apps-gateway` | `*.k8s.<your-domain>` |
| `infra-gateway` | `*.infra.k8s.<your-domain>` |

Change both CIDRs to free addresses on your LAN, outside the DHCP range, or
the router will eventually hand one to a laptop, then create the two wildcard
`A` records. Two more things in the same component decide whether anything
answers on those addresses:

- The `CiliumL2AnnouncementPolicy` in `lb-pools.yaml` answers ARP for them
  from the control-plane nodes only, on interfaces matching a name pattern.
  Change the `nodeSelector` if your control planes should not carry the
  traffic and `interfaces` if the pattern does not match your NICs; L2 needs
  the addresses in the nodes' subnet.
- `payload/platform/cilium/bgp.yaml` also announces them to the router over
  eBGP, and its peer and ASNs must match the inventory (step 5). With no BGP
  router, delete the file.

## 4. Set up the DNS-01 solver

`payload/platform/certificates/cluster-issuers.yaml` is written for **AWS
Route53**. Update:

- `email:` — your address, on both issuers. Let's Encrypt sends expiry notices here.
- `region:` — the Route53 region.

If your DNS is hosted elsewhere, replace the `dns01.route53` solver with the
matching [cert-manager DNS-01 provider](https://cert-manager.io/docs/configuration/acme/dns01/)
and adjust the credential path in OpenBao accordingly.

!!! tip
    Switch `issuerRef` in `certificates.yaml` to `letsencrypt-staging` while you are still iterating. Production allows 5 duplicate certificates per week, and a misconfigured solver burns through that in minutes.

## 5. Describe your hardware

Edit `ansible/inventory.yaml`; every variable is listed under
[Quickstart &rarr; step 2](quickstart.md#setup). The ones a fork must choose
rather than copy:

| Setting | Notes |
| --- | --- |
| `control_plane_vip` | Free address on the nodes' subnet, outside DHCP and distinct from the LoadBalancer pools. Becomes the API endpoint in the cluster certificates. |
| `control_plane_vip_interface` | The NIC kube-vip advertises on; check `ip link` on a provisioned node. |
| `control_plane_endpoint_host` | Set a name on a fresh build, see [Endpoint name](operations/control-plane-vip.md#endpoint-name); a running cluster keeps the VIP. |
| host entries | Replace the hosts with yours. Each needs `ansible_host` and `mac_address` (the NIC that PXE boots), and a DHCP reservation that pairs the two: the node takes its address from DHCP, and the kubeadm configs are rendered from `ansible_host` — see [Quickstart &rarr; Other requirements](quickstart.md#other-requirements). |
| `router_ip`, `router_asn`, `cluster_asn` | The eBGP peer for the Gateway addresses; `payload/platform/cilium/bgp.yaml` must name the same values, since the payload cannot read the inventory — see [Cilium](platform/cilium.md#configuration). |

Then update `payload/platform/cilium/values.yaml`:

- `k8sServiceHost` — a literal, reachable API server address, because Cilium
  replaces `kube-proxy` and cannot reach the API through a Service — see
  [Design Decisions &rarr; Cilium](architecture/decisions.md#cilium-as-cni-replacing-kube-proxy).
- `devices` — the interface prefix Cilium binds to; check `ip link` on a
  provisioned node.

And `payload/platform/kube-vip/daemonset.yaml`, which takes over from the
bootstrap static pod once ArgoCD syncs and cannot read the inventory:

- `address` — the same value as `control_plane_vip`.
- `vip_interface` — the same value as `control_plane_vip_interface`.

A mismatch here does not fail at provisioning. It fails on the first sync, when
the DaemonSet removes the static pods and starts advertising somewhere else.

## 6. Provisioning access

Both Butane templates, `ansible/templates/butane_node_config.yaml.j2` and
`butane_installer_config.yaml.j2`, inject `~/.ssh/id_ed25519.pub` as the
authorized key for the `core` user. Point both at your own key if you use a
different path or algorithm. There is no password,
no console login, and no rescue path short of reprovisioning, so this is the
only way into the nodes afterwards.

## 7. The documentation site

If you want your fork to publish its own copy of these docs, update
`zensical.toml`: `site_url`, `repo_url`, `repo_name`, and `copyright`, and
`docs/CNAME`, which binds the Pages site to a custom domain; delete it if you
have none. The `docs.yaml` workflow then publishes to your own GitHub Pages. Otherwise, delete
`.github/workflows/docs.yaml` to stop the build from running.

## Checklist

Before `make config`:

- [ ] `repoURL` points at your fork in all of `payload/`, the workloads URLs and the previews `owner` at yours
- [ ] Domain replaced throughout `payload/`, `wollbi.ch` renamed or removed
- [ ] LoadBalancer IPs are free addresses on your subnet, and the L2 policy or BGP peer announces them
- [ ] Wildcard DNS records created for both gateways
- [ ] ACME email and DNS-01 provider match your setup
- [ ] `inventory.yaml` describes your nodes, with the right `boot_server_ip`
- [ ] `control_plane_vip` is free, and `control_plane_vip_interface` matches the NIC
- [ ] `address` and `vip_interface` in the kube-vip DaemonSet match those two
- [ ] `k8sServiceHost` and `devices` match your control plane and NICs. On a new
      build, point `k8sServiceHost` at `control_plane_vip` once the VIP answers —
      see [Control Plane VIP](operations/control-plane-vip.md)
- [ ] SSH public key path is correct
- [ ] Changes committed and pushed — ArgoCD reads from Git, not your working tree
