#!/usr/bin/env bash
# Writes the OpenBao policies and Kubernetes auth roles the External Secrets
# Operator logs in with, one role per ClusterSecretStore in
# payload/platform/openbao/cluster-secret-store.yaml.
#
#   make bao-policies
#
# `make bao-init` runs this on a fresh cluster. An initialised cluster is left
# alone by bao-init, so a change here reaches the live one only through this
# target. Every run overwrites all three policies and roles; nothing else is
# touched. See docs/platform/openbao.md#kubernetes-auth.
#
#   role / policy                   store                  reaches
#   external-secrets                openbao                reads every path, writes three
#   external-secrets-apps           openbao-apps           kv/<caller's namespace>/
#   external-secrets-claude-agents  openbao-claude-agents  kv/claude-*
set -euo pipefail

ESO_NAMESPACE="${ESO_NAMESPACE:-external-secrets}"
ESO_SERVICEACCOUNT="${ESO_SERVICEACCOUNT:-external-secrets-vault}"
CLAUDE_NAMESPACE="${CLAUDE_NAMESPACE:-claude-agents}"

# shellcheck source=lib/bao.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/lib/bao.sh"

bao_login

echo "### Policies and roles"

# The apps policy names the caller's namespace through an identity template,
# which addresses the auth mount by its accessor.
accessor="$(bao auth list -format=json \
  | uv run python -c 'import json,sys; print(json.load(sys.stdin)["kubernetes/"]["accessor"])')"
namespace="{{identity.entity.aliases.${accessor}.metadata.service_account_namespace}}"

# Reads everything: Authentik reads each workload's OIDC pair from its
# kv/<app>/config. Writes only where a platform PushSecret writes generated
# values; ESO itself refuses to touch a path it did not create, and no delete
# is granted, so nothing it pushes can remove a secret.
bao_in policy write external-secrets - <<'POLICY' >/dev/null
path "kv/data/*" {
  capabilities = ["read"]
}
path "kv/metadata/*" {
  capabilities = ["read", "list"]
}
path "kv/data/authentik/*" {
  capabilities = ["create", "read", "update"]
}
path "kv/metadata/authentik/*" {
  capabilities = ["create", "read", "update", "list"]
}
path "kv/data/monitoring/*" {
  capabilities = ["create", "read", "update"]
}
path "kv/metadata/monitoring/*" {
  capabilities = ["create", "read", "update", "list"]
}
path "kv/data/velero/*" {
  capabilities = ["create", "read", "update"]
}
path "kv/metadata/velero/*" {
  capabilities = ["create", "read", "update", "list"]
}
POLICY
echo "    external-secrets                policy written (read on kv/, write on authentik/, monitoring/ and velero/)"

bao write auth/kubernetes/role/external-secrets \
  bound_service_account_names="$ESO_SERVICEACCOUNT" \
  bound_service_account_namespaces="$ESO_NAMESPACE" \
  policies=external-secrets \
  ttl=1h >/dev/null
echo "    external-secrets                role bound to ${ESO_NAMESPACE}/${ESO_SERVICEACCOUNT}"

# The same capabilities on the caller's own prefix only. The namespace comes
# from the ServiceAccount token OpenBao validated, not from the ExternalSecret.
bao_in policy write external-secrets-apps - <<POLICY >/dev/null
path "kv/data/${namespace}/*" {
  capabilities = ["create", "read", "update"]
}
path "kv/metadata/${namespace}/*" {
  capabilities = ["create", "read", "update", "list"]
}
POLICY
echo "    external-secrets-apps           policy written (kv/<namespace>/ of the caller)"

# Every namespace, so a new workload needs no change here; the openbao-apps
# store's conditions keep the platform namespaces out.
bao write auth/kubernetes/role/external-secrets-apps \
  bound_service_account_names="$ESO_SERVICEACCOUNT" \
  bound_service_account_namespaces='*' \
  policies=external-secrets-apps \
  ttl=1h >/dev/null
echo "    external-secrets-apps           role bound to */${ESO_SERVICEACCOUNT}"

# Read only: nothing in the sessions' namespace pushes.
bao_in policy write external-secrets-claude-agents - <<'POLICY' >/dev/null
path "kv/data/claude-*" {
  capabilities = ["read"]
}
POLICY
echo "    external-secrets-claude-agents  policy written (read on kv/claude-*)"

bao write auth/kubernetes/role/external-secrets-claude-agents \
  bound_service_account_names="$ESO_SERVICEACCOUNT" \
  bound_service_account_namespaces="$CLAUDE_NAMESPACE" \
  policies=external-secrets-claude-agents \
  ttl=1h >/dev/null
echo "    external-secrets-claude-agents  role bound to ${CLAUDE_NAMESPACE}/${ESO_SERVICEACCOUNT}"
