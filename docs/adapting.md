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
git grep -l 'github.com/JanWelker/homelab' -- payload/
```

Rewrite them to your fork:

```bash
git grep -lz 'github.com/JanWelker/homelab' -- payload/ | \
  xargs -0 sed -i '' 's|github.com/JanWelker/homelab|github.com/YOUR_USER/YOUR_REPO|g'
```

!!! note
    `sed -i ''` is the BSD/macOS form. On Linux use `sed -i` with no argument.

If your fork is private, ArgoCD also needs repository credentials — see the
[ArgoCD private repository docs](https://argo-cd.readthedocs.io/en/stable/operator-manual/declarative-setup/#repositories).

## 2. Choose your domain

Two DNS zones carry all traffic:

| Pattern | Purpose |
| --- | --- |
| `*.k8s.<your-domain>` | user-facing workloads, via `apps-gateway` |
| `*.infra.k8s.<your-domain>` | platform UIs (ArgoCD, Grafana, Hubble, OpenBao, Rook), via `infra-gateway` |

Replace the domain everywhere:

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

The two Gateways take fixed addresses from the Cilium L2 pools in
`payload/platform/cilium/lb-pools.yaml`:

| Gateway | DNS record |
| --- | --- |
| `apps-gateway` | `*.k8s.<your-domain>` |
| `infra-gateway` | `*.infra.k8s.<your-domain>` |

Change both CIDRs to free addresses on your LAN — in the nodes' subnet, since
Cilium announces them over L2 ARP, and outside the DHCP range, or the router
will eventually hand one to a laptop — then create the two wildcard `A`
records.

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

Edit `ansible/inventory.yaml`; the variables are listed under
[Quickstart &rarr; step 2](quickstart.md#setup). Beyond those:

| Setting | Notes |
| --- | --- |
| `control_plane_vip` | Free address on the nodes' subnet, outside DHCP and distinct from the LoadBalancer pools. Becomes the API endpoint in the cluster certificates. |
| `control_plane_vip_interface` | The NIC kube-vip advertises on; check `ip link` on a provisioned node. |
| host entries | Replace the six hosts with yours. Each needs `ansible_host` (static IP) and `mac_address` (the NIC that PXE boots). |
| `control_plane` / `workers` | Group membership decides the node role. |
| `pod_subnet`, `service_subnet` | Only change if they collide with your LAN. |
| `router_ip`, `router_asn`, `cluster_asn` | The eBGP peer for the Gateway addresses; `payload/platform/cilium/bgp.yaml` must name the same values, since the payload cannot read the inventory. `make config` renders the router's FRR config into `output/router/` — see [Cilium](platform/cilium.md#configuration). |

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

`ansible/templates/butane_node_config.yaml.j2` injects
`~/.ssh/id_ed25519.pub` as the authorized key for the `core` user. Point it at
your own key if you use a different path or algorithm. There is no password,
no console login, and no rescue path short of reprovisioning, so this is the
only way into the nodes afterwards.

## 7. The documentation site

If you want your fork to publish its own copy of these docs, update
`zensical.toml`: `site_url`, `repo_url`, `repo_name`, and `copyright`. The
`docs.yaml` workflow then publishes to your own GitHub Pages. Otherwise, delete
`.github/workflows/docs.yaml` to stop the build from running.

## Checklist

Before `make config`:

- [ ] `repoURL` points at your fork in all of `payload/`
- [ ] Domain replaced throughout `payload/`
- [ ] LoadBalancer IPs are free addresses on your subnet
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
