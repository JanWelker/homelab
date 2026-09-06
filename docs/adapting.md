---
description: "What to change to run this project against your own hardware, domain, and Git repository."
---

# Adapting This for Your Cluster

This repository documents one specific homelab. Node names, addresses, the
`wlkr.ch` domain, and the Git repository URL are hardcoded throughout
`payload/` and `ansible/`. Run the [Quickstart](quickstart.md) unchanged and you
get a cluster that syncs from *this* repository and requests certificates for a
domain you don't control.

Which is a fascinating way to discover that GitOps works exactly as advertised:
your cluster will be beautifully, obediently, continuously reconciled to
somebody else's intentions.

Work through this page first. Everything below is a change you make in your own
fork, before step 1 of the Quickstart.

## 1. Fork and repoint ArgoCD

Every ArgoCD `Application` points at this repository by URL. Until you change
them, your cluster pulls its desired state from here — your own commits will
have no effect, and a push here would deploy to your cluster. Neither of those
is a good afternoon.

```bash
git grep -l 'github.com/JanWelker/homelab' -- payload/
```

Rewrite them to your fork:

```bash
git grep -lz 'github.com/JanWelker/homelab' -- payload/ | \
  xargs -0 sed -i '' 's|github.com/JanWelker/homelab|github.com/YOUR_USER/YOUR_REPO|g'
```

!!! note
    `sed -i ''` is the BSD/macOS form. On Linux use `sed -i` with no argument. Getting this backwards creates a file literally named `''` or silently eats your argument, depending on which side of the fence you are standing.

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
`link.argocd.argoproj.io/external-link` annotations that render as links in the
ArgoCD UI.

You must own this domain — the certificates are issued by Let's Encrypt through
a DNS-01 challenge, which requires write access to the zone. `.local`, `.lan`
and your favourite made-up TLD will not work, and finding that out three hours
into a cert-manager debugging session is a rite of passage you can simply skip.

## 3. Point DNS at the gateway IPs

The two Gateways take fixed addresses from the Cilium L2 pool, defined in
`payload/platform/cilium/lb-pools.yaml`:

| Gateway | Default IP | DNS record |
| --- | --- | --- |
| `apps-gateway` | `10.9.2.249` | `*.k8s.<your-domain>` |
| `infra-gateway` | `10.9.2.248` | `*.infra.k8s.<your-domain>` |

Change both CIDRs to free addresses on your LAN — they must be in the nodes'
subnet, since Cilium announces them over L2 ARP — then create the two wildcard
`A` records.

"Free" means free *and* outside the DHCP range. An address that is unused today
and inside the pool is an address your router will hand to a laptop next Tuesday,
and the resulting intermittent outage is genuinely unpleasant to diagnose.

## 4. Set up the DNS-01 solver

`payload/platform/cert-manager/cluster-issuers.yaml` is written for **AWS
Route53**. Update:

- `email:` — your address, on both issuers. Let's Encrypt sends expiry notices here.
- `region:` — the Route53 region (`eu-central-1` by default).

If your DNS is hosted elsewhere, replace the `dns01.route53` solver with the
matching [cert-manager DNS-01 provider](https://cert-manager.io/docs/configuration/acme/dns01/)
and adjust the credential path in OpenBao accordingly.

!!! tip
    Switch `issuerRef` in `certificates.yaml` to `letsencrypt-staging` while you are still iterating. Production allows 5 duplicate certificates per week, and a misconfigured solver will burn through that in about ten minutes of enthusiastic retrying — after which you wait seven days with nothing to show for it.

## 5. Describe your hardware

Edit `ansible/inventory.yaml`:

| Setting | Notes |
| --- | --- |
| `boot_server_ip` | The IP of the machine that will run `make serve`. Baked into the generated PXE menus — see the [PXE troubleshooting table](quickstart.md#troubleshooting-pxe-boot). |
| `control_plane_vip` | Free address on the nodes' subnet, outside DHCP and distinct from the LoadBalancer pools. Becomes the API endpoint in the cluster certificates. |
| `control_plane_vip_interface` | The NIC kube-vip advertises on; check `ip link` on a provisioned node. |
| host entries | Replace the six Norse-named hosts with yours. Each needs `ansible_host` (static IP) and `mac_address` (the NIC that PXE boots). |
| `control_plane` / `workers` | Group membership decides the node role. |
| `install_disk` | Global default is `/dev/nvme0n1`; override per host as `freya` does with `/dev/sda`. |
| `pod_subnet`, `service_subnet` | Only change if they collide with your LAN. |
| `flatcar_version`, `kubernetes_version`, `containerd_version`, `syslinux_version` | Artifact versions to download. |

Then update `payload/platform/cilium/values.yaml`:

- `k8sServiceHost` — the API server address, currently the first control-plane
  node (`10.9.2.1`). Cilium replaces `kube-proxy`, so it cannot reach the API
  through a Service and needs a reachable address here.
- `devices` — the interface prefix Cilium binds to, `"en+"` by default. Linux
  hosts are usually `"en+"` or `"eth+"`; check `ip link` on a provisioned node.

A note on naming your nodes: pick a theme with more members than you currently
have machines. Norse gods scale further than you would think, and nothing is
more annoying than a cluster where the seventh node has to be called `node7`.

## 6. Provisioning access

`ansible/templates/butane_config.yaml.j2` injects
`~/.ssh/id_ed25519.pub` as the authorized key for the `core` user. Point it at
your own key if you use a different path or algorithm — this is the only way
into the nodes afterwards, so get it right before the first boot.

There is no password, no console login, and no rescue path short of
reprovisioning. Immutable infrastructure is wonderful right up until the moment
you have locked yourself out of all six machines at once, at which point it is
merely instructive.

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
- [ ] `k8sServiceHost` and `devices` match your control plane and NICs. On a new
      build, point `k8sServiceHost` at `control_plane_vip` once the VIP answers —
      see [Control Plane VIP](operations/control-plane-vip.md)
- [ ] SSH public key path is correct
- [ ] Changes committed and pushed — ArgoCD reads from Git, not your working tree

That last one deserves emphasis. ArgoCD cannot see your uncommitted brilliance.
Every "why is it not picking up my change" incident in the history of GitOps has
ended the same way, and it ends with `git push`.
