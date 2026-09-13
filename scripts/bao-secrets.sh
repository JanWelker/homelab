#!/usr/bin/env bash
# Populates the four kv paths the cluster reads through ExternalSecrets.
#
#   make bao-secrets
#
# Three of the values cannot be generated -- they belong to accounts outside
# this cluster -- and are read from the environment:
#
#   CERT_MANAGER_KEY_ID / CERT_MANAGER_SECRET_KEY   Route53, TXT records only
#   EXTERNAL_DNS_KEY_ID / EXTERNAL_DNS_SECRET_KEY   Route53, A and TXT records
#   SMTP_PASSWORD                                   Alertmanager's mail account
#
# Two IAM users on purpose: cert-manager only ever writes _acme-challenge TXT
# records, external-dns creates and deletes A records for every hostname. See
# payload/platform/external-dns/route53-credentials.yaml.
#
# Everything under kv/authentik/config is generated here, including the OIDC
# client credentials ArgoCD and Grafana read back from the same path.
#
# Existing paths are left alone. Rewriting kv/authentik/config on a running
# cluster rotates Authentik's Postgres password out from under its database,
# which is a considerably worse afternoon than it sounds. FORCE=1 overwrites
# anyway, and asks first.
set -euo pipefail

NAMESPACE="${NAMESPACE:-openbao}"
POD="${POD:-openbao-0}"
KEYFILE="${KEYFILE:-output/credentials/openbao-init.json}"
FORCE="${FORCE:-0}"

bao() { kubectl -n "$NAMESPACE" exec "$POD" -- bao "$@"; }
bao_in() { kubectl -n "$NAMESPACE" exec -i "$POD" -- bao "$@"; }

missing=()
for var in CERT_MANAGER_KEY_ID CERT_MANAGER_SECRET_KEY \
           EXTERNAL_DNS_KEY_ID EXTERNAL_DNS_SECRET_KEY \
           SMTP_PASSWORD; do
  [ -n "${!var:-}" ] || missing+=("$var")
done

[ "${#missing[@]}" -eq 0 ] || {
  echo "ERROR: these have to come from outside the cluster and are not set:" >&2
  printf '         %s\n' "${missing[@]}" >&2
  echo >&2
  echo "       export them and re-run, e.g." >&2
  echo "         CERT_MANAGER_KEY_ID=AKIA... CERT_MANAGER_SECRET_KEY=... \\" >&2
  echo "         EXTERNAL_DNS_KEY_ID=AKIA... EXTERNAL_DNS_SECRET_KEY=... \\" >&2
  echo "         SMTP_PASSWORD=... make bao-secrets" >&2
  exit 1
}

[ -f "$KEYFILE" ] || {
  echo "ERROR: ${KEYFILE} not found -- run 'make bao-init' first, or log in by hand." >&2
  exit 1
}

sealed="$(kubectl -n "$NAMESPACE" exec "$POD" -- bao status -format=json 2>/dev/null \
  | uv run python -c 'import json,sys; print(json.load(sys.stdin).get("sealed",""))' 2>/dev/null || true)"
[ "$sealed" = "False" ] || {
  echo "ERROR: ${POD} is sealed. Run 'make bao-unseal' first." >&2
  exit 1
}

root_token="$(uv run python -c '
import json, sys
with open(sys.argv[1]) as handle:
    print(json.load(handle)["root_token"])
' "$KEYFILE")"

printf '%s' "$root_token" | bao_in login - >/dev/null
cleanup() {
  kubectl -n "$NAMESPACE" exec "$POD" -- sh -c 'rm -f "$HOME/.bao-token" /tmp/bao-secret.json' >/dev/null 2>&1 || true
}
trap cleanup EXIT

exists() {
  bao kv get -mount=kv "$1" >/dev/null 2>&1 && echo yes || echo no
}

# Values reach the pod as a JSON file on stdin rather than as arguments, so
# they stay out of both this shell's history and the pod's process list.
put() {
  local path="$1"
  shift
  if [ "$(exists "$path")" = "yes" ] && [ "$FORCE" != "1" ]; then
    printf '  %-24s exists, left alone\n' "kv/${path}"
    return
  fi
  uv run python -c '
import json, sys
print(json.dumps(dict(pair.split("=", 1) for pair in sys.argv[1:])))
' "$@" \
    | kubectl -n "$NAMESPACE" exec -i "$POD" -- sh -c 'umask 077; cat > /tmp/bao-secret.json'
  bao kv put -mount=kv "$path" @/tmp/bao-secret.json >/dev/null
  kubectl -n "$NAMESPACE" exec "$POD" -- rm -f /tmp/bao-secret.json
  printf '  %-24s written\n' "kv/${path}"
}

if [ "$FORCE" = "1" ]; then
  cat <<EOF

FORCE=1 overwrites paths that already exist.

Rewriting kv/authentik/config rotates Authentik's Postgres password while
Postgres is still using the old one, and invalidates the OIDC client secrets
ArgoCD and Grafana authenticate with. On a running cluster that is an outage.

EOF
  [ -t 0 ] || { echo "ERROR: refusing to overwrite without a terminal to confirm at" >&2; exit 1; }
  printf 'Type OVERWRITE to continue: '
  read -r reply
  [ "$reply" = "OVERWRITE" ] || { echo "Aborted."; exit 1; }
  echo
fi

rand_b64() { openssl rand -base64 "$1" | tr -d '\n'; }
rand_hex() { openssl rand -hex "$1"; }

put cert-manager/route53 \
  "access-key-id=${CERT_MANAGER_KEY_ID}" \
  "secret-access-key=${CERT_MANAGER_SECRET_KEY}"

put external-dns/route53 \
  "access-key-id=${EXTERNAL_DNS_KEY_ID}" \
  "secret-access-key=${EXTERNAL_DNS_SECRET_KEY}"

put authentik/config \
  "secret-key=$(rand_b64 60)" \
  "postgres-password=$(rand_b64 32)" \
  "bootstrap-password=$(rand_b64 24)" \
  "bootstrap-token=$(rand_hex 32)" \
  "argocd-client-id=$(rand_hex 16)" \
  "argocd-client-secret=$(rand_b64 48)" \
  "grafana-client-id=$(rand_hex 16)" \
  "grafana-client-secret=$(rand_b64 48)"

put monitoring/smtp \
  "password=${SMTP_PASSWORD}"

cat <<'EOF'

Done. External Secrets refreshes on its own; to watch it land:

  kubectl get externalsecret -A
  kubectl get clustersecretstore openbao -o jsonpath='{.status.conditions}'

The Authentik admin password is generated, never printed, and is what you log
in with as akadmin:

  kubectl -n openbao exec openbao-0 -- bao kv get -mount=kv \
    -field=bootstrap-password authentik/config
EOF
