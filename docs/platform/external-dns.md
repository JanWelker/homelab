---
description: "external-dns publishes Route53 records from HTTPRoutes, closing the last manual step in adding a hostname."
---

# external-dns

[external-dns](https://kubernetes-sigs.github.io/external-dns/) creates the
Route53 records for this cluster's hostnames from the `HTTPRoute` objects that
already declare them. Without it every hostname is a record made by hand in
the AWS console, a step that lives nowhere in the repository and leaves a
record pointing at nothing when the workload goes.

## At a glance

| | |
| --- | --- |
| Namespace | `external-dns` |
| Stage | `06-certificates`, with the Route53 credentials it shares a source with |
| Depends on | [External Secrets](external-secrets.md) for its Route53 credential, [Gateway API](gateway-api.md) for the HTTPRoutes it reads |
| If it is down | New hostnames get no DNS record. Existing records are left alone |
| Health check | `kubectl -n external-dns logs deploy/external-dns --tail=50` |
| Files | `payload/platform/external-dns/` |

## Configuration

| Setting | Why |
| --- | --- |
| Source `gateway-httproute` | `HTTPRoute` is the only thing here that publishes a hostname; the address comes from the route's parent `Gateway`, so nothing is written down twice |
| Domain filter | Nothing outside that subtree is touched |
| TXT registry with an owner id | A companion `_externaldns.*` TXT record stamps every record it creates, and it only modifies or deletes records carrying that stamp. Hand-made records in the same zone are invisible to it |
| Policy `sync` | Deleting an HTTPRoute removes its record. Safe only because of the registry; without it `sync` would happily delete your MX records |
| `--aws-zone-match-parent` | The records live in the `wlkr.ch` zone, not a zone of their own |
| Credentials as a file (`AWS_SHARED_CREDENTIALS_FILE`), not environment variables | The environment puts a key that can repoint every hostname into `kubectl describe pod`, crash dumps and every child process. The `ExternalSecret` templates an INI `credentials` key, the only key mounted; the original keys stay in the Secret so nothing still reading them breaks |

The credential is a separate IAM user and OpenBao path from cert-manager's —
see [OpenBao](openbao.md#kv-layout) for why. Its policy needs
`route53:ChangeResourceRecordSets` on the hosted zone, plus
`route53:ListHostedZones` and `route53:ListResourceRecordSets`.

## Usage

Nothing beyond the `HTTPRoute` you were already writing — see
[Gateway API](gateway-api.md#usage). The credential is stored once,
by `make bao-secrets` or by hand:

```bash
bao kv put kv/external-dns/route53 \
  access-key-id="AKIA..." \
  secret-access-key="..."
```

## Health check

```bash
kubectl -n external-dns logs deploy/external-dns --tail=50
dig +short argo.infra.k8s.wlkr.ch
kubectl -n external-dns port-forward deploy/external-dns 7979:7979
curl -s localhost:7979/metrics | grep endpoints_total
# external_dns_source_endpoints_total   8   <- hostnames it can see
# external_dns_registry_endpoints_total 0   <- records it owns
```

`All records are already up to date, there are no changes for the matching
hosted zones` means it found no zone to change: sources fine, provider not,
which on Route53 is usually zone matching.

## Pitfalls

!!! note "A record that will not update is one external-dns does not own"
    Check for the matching `_externaldns.` TXT record in Route53. To adopt a hand-made record, create that TXT entry or delete the record and let external-dns recreate it.
