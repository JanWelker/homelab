---
description: "Add a component to payload/platform/: the Application, the security labels and policies, the version pin and the documentation page it needs before its first sync."
---

# Adding a Platform Component

A platform component is a directory under `payload/platform/` with exactly one
`application.yaml`; the `platform` ApplicationSet turns it into an ArgoCD
Application on the next poll. Workloads go in the
[other repository](add-workload.md). Why the platform is shaped this way is in
[GitOps Strategy](../architecture/gitops.md).

## Steps

1. **Create the directory** with a complete `application.yaml` copied from a
   sibling: the generator copies its labels, annotations, finalizers and
   `spec` verbatim, so a fragment is not an Application. Keep the
   [sync policy](../architecture/gitops.md#sync-policy) as it is — every
   component carries the same one, `retry` included, and a chart that lands
   before its CRDs stays failed without it.

2. **Pin the version once**, as `targetRevision` in that file. The `Makefile`
   reads the bootstrap versions out of these manifests and Renovate bumps
   them there — see [Version pins](../architecture/gitops.md#version-pins).

3. **Put each resource with what it needs, not with what it configures.**
   Nothing orders the Applications, so a `ClusterSecretStore` sits with
   OpenBao and the issuers are their own component — see
   [Bootstrap convergence](../architecture/gitops.md#bootstrap-convergence).
   A resource whose CRD comes from another component gets
   `argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true`.

4. **Label the namespace** in `payload/platform/security/pod-security.yaml`
   with the Pod Security Admission level it demonstrably needs, one sentence
   of why next to any `privileged` — see
   [Security Policies](../platform/security-policies.md#pod-security-admission).

5. **Write its network policy** as
   `payload/platform/security/network-policies/<namespace>.yaml`: the
   `default-ingress` and `default-egress` deny pair plus one policy per
   workload that needs more, copied from the namespace most like it. New
   HTTP rules start as `http: [{}]` until the
   [rollout](../platform/security-policies.md#rollout) has recorded a week
   of requests.

6. **Expose a UI** with an `HTTPRoute` on `infra-gateway` next to the
   component, and put it behind Authentik — see
   [Gateway API](../platform/gateway-api.md#usage) and
   [Authentik](../platform/authentik.md). A route that crosses namespaces
   needs a `ReferenceGrant` in the target namespace.

7. **Check Renovate sees the pin.** Chart versions are covered; an image tag
   inside `valuesObject` needs a `# renovate:` comment — see
   [Maintenance](maintenance.md).

8. **Write the page** under `docs/platform/` with the shared skeleton — intro,
   At a glance, Configuration, Usage, Health check, Pitfalls, Recovery only
   with a real runbook — add it to the nav in `zensical.toml` and to the
   [Components table](../platform/index.md#components). The strict docs build
   fails on a page the nav does not list.

9. **Open the PR** and run the checks in [Contributing](contributing.md).
   After the merge the Application appears within one polling interval; a
   Degraded one is usually waiting on a CRD, a Secret or OpenBao — see
   [Troubleshooting](../operations/troubleshooting.md).
