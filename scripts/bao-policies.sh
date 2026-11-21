#!/usr/bin/env bash
# Writes the OpenBao policies and Kubernetes auth roles the External Secrets
# Operator logs in with, one role per ClusterSecretStore in
# payload/platform/openbao/cluster-secret-store.yaml, and the one the nightly
# snapshot CronJob in payload/platform/backup/openbao-snapshot.yaml uses.
#
#   make bao-policies
#
# `make bao-init` runs this on a fresh cluster. An initialised cluster is left
# alone by bao-init, so a change here reaches the live one only through this
# target. Every run overwrites all four policies and roles; nothing else is
# touched. See docs/platform/openbao.md#kubernetes-auth.
#
#   role / policy                   store                  reaches
#   external-secrets                openbao                the platform's paths, writes three
#   external-secrets-apps           openbao-apps           kv/<caller's namespace>/
#   external-secrets-claude-agents  openbao-claude-agents  kv/claude-*
#   openbao-snapshot                (backup CronJob)       the Raft snapshot
set -euo pipefail

ESO_NAMESPACE="${ESO_NAMESPACE:-external-secrets}"
ESO_SERVICEACCOUNT="${ESO_SERVICEACCOUNT:-external-secrets-vault}"
CLAUDE_NAMESPACE="${CLAUDE_NAMESPACE:-claude-agents}"

# shellcheck source=lib/bao.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/lib/bao.sh"

bao_login_root_or_generated

echo "### Policies and roles"

# The apps policy names the caller's namespace through an identity template,
# which addresses the auth mount by its accessor.
accessor="$(bao auth list -format=json \
  | uv run python -c 'import json,sys; print(json.load(sys.stdin)["kubernetes/"]["accessor"])')"
namespace="{{identity.entity.aliases.${accessor}.metadata.service_account_namespace}}"

# The paths the platform namespaces read, and no others: the agent sessions'
# tokens stay out of reach. Authentik also reads each workload's OIDC pair, at
# kv/<app>/oidc or kv/<app>/config. Writes only where a platform PushSecret
# writes generated values; ESO itself refuses to touch a path it did not
# create, and no delete is granted, so nothing it pushes can remove a secret.
bao_in policy write external-secrets - <<'POLICY' >/dev/null
path "kv/data/arc-runners/*" {
  capabilities = ["read"]
}
path "kv/metadata/arc-runners/*" {
  capabilities = ["read", "list"]
}
path "kv/data/cilium/*" {
  capabilities = ["read"]
}
path "kv/metadata/cilium/*" {
  capabilities = ["read", "list"]
}
path "kv/data/cert-manager/*" {
  capabilities = ["read"]
}
path "kv/metadata/cert-manager/*" {
  capabilities = ["read", "list"]
}
path "kv/data/external-dns/*" {
  capabilities = ["read"]
}
path "kv/metadata/external-dns/*" {
  capabilities = ["read", "list"]
}
path "kv/data/+/oidc" {
  capabilities = ["read"]
}
path "kv/metadata/+/oidc" {
  capabilities = ["read", "list"]
}
path "kv/data/+/config" {
  capabilities = ["read"]
}
path "kv/metadata/+/config" {
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
echo "    external-secrets                policy written (write on authentik/, monitoring/ and velero/, read on the other platform paths)"

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

# The namespaces the openbao-apps store admits, read from the manifest so the
# list is written once; a new workload is one line there, then this target.
apps_namespaces="$(uv run python -c '
import sys, yaml
with open(sys.argv[1]) as handle:
    for doc in yaml.safe_load_all(handle):
        if doc and doc.get("kind") == "ClusterSecretStore" and doc["metadata"]["name"] == "openbao-apps":
            print(",".join(n for c in doc["spec"]["conditions"] for n in c.get("namespaces", [])))
' "$(dirname "${BASH_SOURCE[0]}")/../payload/platform/openbao/cluster-secret-store.yaml")"
[ -n "$apps_namespaces" ] || {
  echo "ERROR: no namespaces list on the openbao-apps store in cluster-secret-store.yaml." >&2
  exit 1
}

bao write auth/kubernetes/role/external-secrets-apps \
  bound_service_account_names="$ESO_SERVICEACCOUNT" \
  bound_service_account_namespaces="$apps_namespaces" \
  policies=external-secrets-apps \
  ttl=1h >/dev/null
echo "    external-secrets-apps           role bound to ${apps_namespaces}/${ESO_SERVICEACCOUNT}"

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

# The snapshot is the whole barrier-encrypted store; reading it reveals nothing
# without the unseal keys, and this policy grants nothing else.
bao_in policy write openbao-snapshot - <<'POLICY' >/dev/null
path "sys/storage/raft/snapshot" {
  capabilities = ["read"]
}
POLICY
echo "    openbao-snapshot                policy written (read on sys/storage/raft/snapshot)"

BACKUP_NAMESPACE="${BACKUP_NAMESPACE:-backup}"
bao write auth/kubernetes/role/openbao-snapshot \
  bound_service_account_names=openbao-snapshot \
  bound_service_account_namespaces="$BACKUP_NAMESPACE" \
  policies=openbao-snapshot \
  ttl=10m >/dev/null
echo "    openbao-snapshot                role bound to ${BACKUP_NAMESPACE}/openbao-snapshot"
