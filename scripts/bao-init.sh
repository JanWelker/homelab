#!/usr/bin/env bash
# One-time OpenBao bootstrap: initialise, unseal, and configure everything the
# cluster needs before an ExternalSecret can resolve.
#
#   make bao-init
#
# Equivalent to docs/platform/openbao.md#bootstrap done by hand, which is four
# commands, fifteen unseal prompts and a policy heredoc. It runs:
#
#   1. bao operator init -key-shares=5 -key-threshold=3
#   2. unseal all three replicas
#   3. enable the kv v2 engine at kv/
#   4. enable the kubernetes auth method and point it at the TokenReview API
#   5. write the external-secrets policy and the role bound to ESO's
#      ServiceAccount
#   6. enable the audit devices (scripts/bao-audit.sh)
#
# Steps 3-6 are skipped individually if they are already in place, so this is
# safe to re-run against a half-finished bootstrap. Step 1 is not re-runnable
# by design: an initialised cluster is left alone.
set -euo pipefail

NAMESPACE="${NAMESPACE:-openbao}"
POD="${POD:-openbao-0}"
KEYFILE="${KEYFILE:-output/credentials/openbao-init.json}"
KEY_SHARES="${KEY_SHARES:-5}"
KEY_THRESHOLD="${KEY_THRESHOLD:-3}"
ESO_NAMESPACE="${ESO_NAMESPACE:-external-secrets}"
ESO_SERVICEACCOUNT="${ESO_SERVICEACCOUNT:-external-secrets-vault}"

bao() { kubectl -n "$NAMESPACE" exec "$POD" -- bao "$@"; }
bao_in() { kubectl -n "$NAMESPACE" exec -i "$POD" -- bao "$@"; }

kubectl -n "$NAMESPACE" get pod "$POD" >/dev/null 2>&1 || {
  echo "ERROR: pod ${POD} not found in ${NAMESPACE}. Has ArgoCD synced OpenBao yet?" >&2
  exit 1
}

initialized="$(kubectl -n "$NAMESPACE" exec "$POD" -- bao status -format=json 2>/dev/null \
  | uv run python -c 'import json,sys; print(json.load(sys.stdin).get("initialized",""))' 2>/dev/null || true)"

if [ "$initialized" = "True" ]; then
  echo "${POD} is already initialised -- nothing to do here."
  echo "To unseal it after a restart: make bao-unseal"
  exit 0
fi

mkdir -p "$(dirname "$KEYFILE")"
chmod 0700 "$(dirname "$KEYFILE")"

[ -e "$KEYFILE" ] && {
  echo "ERROR: ${KEYFILE} already exists but the cluster is not initialised." >&2
  echo "       Those keys unseal nothing. Move them out of the way first." >&2
  exit 1
}

echo "### Initialising ${POD} (${KEY_SHARES} shares, threshold ${KEY_THRESHOLD})"

umask 077
bao operator init \
  -key-shares="$KEY_SHARES" \
  -key-threshold="$KEY_THRESHOLD" \
  -format=json > "$KEYFILE"
chmod 0600 "$KEYFILE"

echo "    keys and root token written to ${KEYFILE}"
echo
echo "### Unsealing"
NAMESPACE="$NAMESPACE" KEYFILE="$KEYFILE" scripts/bao-unseal.sh

root_token="$(uv run python -c '
import json, sys
with open(sys.argv[1]) as handle:
    print(json.load(handle)["root_token"])
' "$KEYFILE")"

# `bao login -` caches the token in the pod so the calls below do not carry it
# in their arguments. Cleared again at the end.
printf '%s' "$root_token" | bao_in login - >/dev/null

cleanup() {
  kubectl -n "$NAMESPACE" exec "$POD" -- sh -c 'rm -f "$HOME/.bao-token"' >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo
echo "### Configuring"

has_mount() {
  bao secrets list -format=json 2>/dev/null \
    | uv run python -c 'import json,sys; print("yes" if sys.argv[1] in json.load(sys.stdin) else "no")' "$1" 2>/dev/null || echo no
}

has_auth() {
  bao auth list -format=json 2>/dev/null \
    | uv run python -c 'import json,sys; print("yes" if sys.argv[1] in json.load(sys.stdin) else "no")' "$1" 2>/dev/null || echo no
}

if [ "$(has_mount 'kv/')" = "yes" ]; then
  echo "    kv/ engine            already enabled"
else
  bao secrets enable -path=kv -version=2 kv >/dev/null
  echo "    kv/ engine            enabled (v2)"
fi

if [ "$(has_auth 'kubernetes/')" = "yes" ]; then
  echo "    kubernetes auth       already enabled"
else
  bao auth enable kubernetes >/dev/null
  echo "    kubernetes auth       enabled"
fi

# Written every run: it is idempotent, and it is the one setting that silently
# breaks every ExternalSecret if it drifts.
bao write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc" >/dev/null
echo "    kubernetes auth       pointed at the in-cluster TokenReview API"

bao_in policy write external-secrets - <<'EOF' >/dev/null
path "kv/data/*" {
  capabilities = ["read"]
}
path "kv/metadata/*" {
  capabilities = ["read", "list"]
}
EOF
echo "    external-secrets      policy written (read-only on kv/)"

bao write auth/kubernetes/role/external-secrets \
  bound_service_account_names="$ESO_SERVICEACCOUNT" \
  bound_service_account_namespaces="$ESO_NAMESPACE" \
  policies=external-secrets \
  ttl=1h >/dev/null
echo "    external-secrets      role bound to ${ESO_NAMESPACE}/${ESO_SERVICEACCOUNT}"

NAMESPACE="$NAMESPACE" POD="$POD" KEYFILE="$KEYFILE" scripts/bao-audit.sh

cat <<EOF

Done. OpenBao is initialised, unsealed and ready to be populated:

  make bao-secrets

>>> ${KEYFILE} now holds the 5 unseal keys and the root token, in plaintext,
>>> on this machine. They protect every other secret the cluster has, and
>>> nothing else can recover them. Copy them into a password manager now, and
>>> keep them somewhere that does not require this cluster to be running in
>>> order to read.
>>>
>>> Deleting the file afterwards is the correct end state. \`make bao-unseal\`
>>> needs it, so unsealing goes back to being manual -- which is the trade the
>>> rest of this repository's security posture already makes.
EOF
