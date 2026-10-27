#!/usr/bin/env bash
# Replaces the root token `make bao-init` used to keep with the operator's
# userpass login, on a cluster initialised before bao-init did that itself.
#
#   make bao-rotate-root
#
# It logs in with the root token in output/credentials/openbao-init.json,
# creates the operator policy and login (bao_create_operator in lib/bao.sh),
# writes the password to output/credentials/openbao-operator.json (0600),
# checks the login works, and only then revokes the root token. Safe to re-run:
# with the root token already revoked it only checks the operator login.
#
# When root is needed afterwards -- a policy, an auth method, a snapshot
# restore -- generate a short-lived one with the unseal shares; see
# docs/platform/openbao.md#generating-a-root-token.
set -euo pipefail

# shellcheck source=lib/bao.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/lib/bao.sh"

[ -f "$KEYFILE" ] || {
  echo "ERROR: ${KEYFILE} not found; this needs its root token." >&2
  exit 1
}

bao_require_unsealed
trap bao_cleanup EXIT

if ! bao_login_root; then
  if [ -f "$OPERATOR_FILE" ] && bao_verify_operator; then
    echo "The root token is already revoked and the operator login works -- nothing to do."
    exit 0
  fi
  echo "ERROR: the root token in ${KEYFILE} was refused and there is no working" >&2
  echo "       operator login. See docs/platform/openbao.md#generating-a-root-token" >&2
  exit 1
fi

echo "### Creating the operator login"
bao_create_operator

bao_verify_operator || {
  echo "ERROR: the operator login does not work; the root token was NOT revoked." >&2
  exit 1
}
echo "    operator login        verified (kv/ create and delete)"

bao_revoke_root
echo "    root token            revoked"

cat <<EOF

Done. make bao-secrets and make claude-session-pat log in as the operator now.

>>> ${OPERATOR_FILE} reads and writes every kv/ path. Copy it to the
>>> password manager next to the unseal keys. The root_token in ${KEYFILE}
>>> is dead and can be deleted from your copies.
EOF
