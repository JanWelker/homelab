---
description: "external-dns publishes Route53 records from HTTPRoutes, closing the last manual step in adding a hostname."
---

# external-dns

[external-dns](https://kubernetes-sigs.github.io/external-dns/) creates the
Route53 records for this cluster's hostnames, from the `HTTPRoute` objects that
already declare them.

Without it, every hostname needs a record created by hand in the AWS console —
while cert-manager automates the *certificate* for the same name, through the
same zone, with the same credentials. Adding a workload would mean remembering a
step that lives nowhere in the repository, and removing one would leave a record
pointing at nothing.

## How it decides what to publish

| Setting | Value | Why |
| --- | --- | --- |
| Source | `gateway-httproute` | `HTTPRoute` is the only thing here that publishes a hostname. Ingress is unused, and Services are reached through a Gateway rather than directly |
| Domain filter | `k8s.wlkr.ch` | Nothing outside that subtree is touched |
| Registry | `txt`, owner `homelab-k8s` | Ownership marker on every record it creates |
| Policy | `sync` | Deleting an HTTPRoute removes its record |

The address comes from the `HTTPRoute`'s parent `Gateway` — so a route attached
to `infra-gateway` resolves to `10.9.2.248`, and one on `apps-gateway` to
`10.9.2.249`, without either address being written down again.

!!! note "Why `sync` is safe here"
    `sync` lets external-dns **delete** records, which is usually where people
    reach for `upsert-only` instead. It is safe because of the TXT registry: for
    every record it creates, external-dns writes a companion
    `_externaldns.*` TXT record stamped with `homelab-k8s`, and it will only
    modify or delete records carrying that stamp. Anything created by hand in
    the same zone is invisible to it. Without the registry, `sync` would be a
    way to lose hand-made records.

## Adding a hostname

Nothing beyond the `HTTPRoute` you were already writing:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: my-app
  namespace: my-app
spec:
  parentRefs:
    - name: apps-gateway
      namespace: kube-system
  hostnames:
    - "my-app.k8s.wlkr.ch"
  rules:
    - backendRefs:
        - name: my-app
          port: 80
```

The certificate is already covered by the wildcard on the Gateway, so nothing
outside the `HTTPRoute` needs touching.

## Credentials

A **separate** IAM user and OpenBao path from cert-manager's, at
`kv/external-dns/route53`:

```bash
bao kv put kv/external-dns/route53 \
  access-key-id="AKIA..." \
  secret-access-key="..."
```

They are split because the blast radii differ. cert-manager writes only
`_acme-challenge` TXT records, and a stolen key means someone can issue
certificates for the zone. external-dns creates and deletes A and TXT records,
and a stolen key means someone can repoint hostnames.

The policy needs `route53:ChangeResourceRecordSets` on the hosted zone, plus
`route53:ListHostedZones` and `route53:ListResourceRecordSets`.

## Checking it works

```bash
kubectl -n external-dns logs deploy/external-dns --tail=50
dig +short argo.infra.k8s.wlkr.ch
```

A record that will not update is usually one external-dns does not own — check
for the matching `_externaldns.` TXT record in Route53. Adopting a hand-made
record means creating that TXT entry, or deleting the record and letting
external-dns recreate it.

## Directory Structure

```text
external-dns/
├── application.yaml            # ArgoCD Application (Helm: external-dns)
└── route53-credentials.yaml    # ExternalSecret: scoped Route53 IAM user
```
