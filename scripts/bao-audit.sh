#!/usr/bin/env bash
# Enable OpenBao's audit devices: a file on the audit PVC and stdout, which
# Alloy ships to Loki. Idempotent: a device already at its path is left alone.
#
#   make bao-audit
#
# Run by bao-init on a fresh cluster; run by hand on one initialised before
# this existed. See docs/platform/openbao.md#audit-devices
set -euo pipefail

NAMESPACE="${NAMESPACE:-openbao}"
POD="${POD:-openbao-0}"
KEYFILE="${KEYFILE:-output/credentials/openbao-init.json}"
AUDIT_FILE="${AUDIT_FILE:-/openbao/audit/audit.log}"

bao() { kubectl -n "$NAMESPACE" exec "$POD" -- bao "$@"; }
bao_in() { kubectl -n "$NAMESPACE" exec -i "$POD" -- bao "$@"; }

# The root token from bao-init's key file, or one pasted in. Never an argument.
if [ -n "${BAO_TOKEN:-}" ]; then
  token="$BAO_TOKEN"
elif [ -f "$KEYFILE" ]; then
  token="$(uv run python -c '
import json, sys
with open(sys.argv[1]) as handle:
    print(json.load(handle)["root_token"])
' "$KEYFILE")"
else
  read -rs -p "OpenBao root token: " token
  echo
fi

printf '%s' "$token" | bao_in login - >/dev/null
cleanup() {
  kubectl -n "$NAMESPACE" exec "$POD" -- sh -c 'rm -f "$HOME/.bao-token"' >/dev/null 2>&1 || true
}
trap cleanup EXIT

has_audit() {
  bao audit list -format=json 2>/dev/null \
    | uv run python -c 'import json,sys; print("yes" if sys.argv[1] in json.load(sys.stdin) else "no")' "$1" 2>/dev/null || echo no
}

# Two devices, deliberately: OpenBao refuses every request while no enabled
# device can be written to, so the file on the PVC and stdout back each other.
if [ "$(has_audit 'file/')" = "yes" ]; then
  echo "    audit file            already enabled"
else
  bao audit enable -path=file file file_path="$AUDIT_FILE" >/dev/null
  echo "    audit file            enabled at ${AUDIT_FILE}"
fi

if [ "$(has_audit 'stdout/')" = "yes" ]; then
  echo "    audit stdout          already enabled"
else
  bao audit enable -path=stdout file file_path=stdout >/dev/null
  echo "    audit stdout          enabled (Alloy ships it to Loki)"
fi
