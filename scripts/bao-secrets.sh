#!/usr/bin/env bash
# Populates the six kv paths the cluster reads through ExternalSecrets.
#
#   make bao-secrets
#
# Seven of the values cannot be generated -- they belong to accounts outside this
# cluster -- and are prompted for, one per line, with the input hidden:
#
#   CERT_MANAGER_KEY_ID / CERT_MANAGER_SECRET_KEY   Route53, TXT records only
#   EXTERNAL_DNS_KEY_ID / EXTERNAL_DNS_SECRET_KEY   Route53, A and TXT records
#   SMTP_USERNAME / SMTP_PASSWORD                   Alertmanager's mail account
#   SMTP_TO                                         where alert mail is delivered
#
# The two addresses are not secrets in the credential sense, but they are kept
# out of the repository, so they live here with the password. See
# payload/platform/monitoring/alertmanager-config.yaml.
#
# Prompting rather than reading the environment is deliberate. A secret
# containing `#` is truncated at it on a command line, and one containing `!`
# is mangled by history expansion in an interactive bash -- both silently, and
# both producing a credential that looks fine in OpenBao and fails to
# authenticate weeks later. `read -rs` takes the line exactly as typed:
# `#`, `!`, `$`, backslashes, quotes and spaces all survive.
#
# The matching environment variable is still honoured when it is already set,
# for non-interactive use. Set those with a quoting style that survives the
# characters in them -- single quotes, or `read -rs` into the variable first.
#
# Two IAM users on purpose: cert-manager only ever writes _acme-challenge TXT
# records, external-dns creates and deletes A records for every hostname. See
# payload/platform/external-dns/route53-credentials.yaml.
#
# Everything under kv/authentik/config is generated here, including the OIDC
# client credentials ArgoCD and Grafana read back from the same path. Grafana's
# break-glass admin password under kv/monitoring/grafana-admin is generated too.
#
# kv/nextcloud/config is generated as well, and is deliberately its own path
# rather than four more keys under kv/authentik/config. A `bao kv put` replaces
# a path wholesale, so every application that kept its client credentials there
# would make adding the next one an Authentik outage. Authentik reads the two
# OIDC keys from here through the same ExternalSecret it reads its own config
# with; Nextcloud reads all four. See payload/platform/authentik/secrets.yaml.
#
# Each path that already exists is named, and overwriting it is asked about one
# path at a time -- so a single rotated Route53 key does not mean retyping the
# other secrets, and does not put kv/authentik/config anywhere near the
# blast radius. Rewriting that one rotates Authentik's Postgres password out
# from under its database, so it asks for a typed confirmation rather than a
# keystroke -- and keeps asking even under FORCE=1, which answers yes to the
# others for non-interactive use.
set -euo pipefail

NAMESPACE="${NAMESPACE:-openbao}"
POD="${POD:-openbao-0}"
KEYFILE="${KEYFILE:-output/credentials/openbao-init.json}"
FORCE="${FORCE:-0}"

bao() { kubectl -n "$NAMESPACE" exec "$POD" -- bao "$@"; }
bao_in() { kubectl -n "$NAMESPACE" exec -i "$POD" -- bao "$@"; }

# --- Preflight -------------------------------------------------------------
#
# Logging in has to happen before anything is prompted for, because which
# credentials are needed depends on which paths already exist, and that cannot
# be known without reading OpenBao first.

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
  kubectl -n "$NAMESPACE" exec "$POD" -- sh -c 'rm -f "$HOME/.bao-token"' >/dev/null 2>&1 || true
}
trap cleanup EXIT

exists() {
  bao kv get -mount=kv "$1" >/dev/null 2>&1
}

# --- Decide what to write --------------------------------------------------
#
# Sets the named variable to 1 (write) or 0 (skip). A path that does not exist
# yet is always written: there is nothing to lose and this is what makes a
# first run on a fresh cluster need no answers at all.
#
# `danger` marks a path whose contents other live components authenticate
# against, where a rewrite is an outage rather than an inconvenience. Those ask
# for a typed word, because y is far too easy to hit by reflex.
decide() {
  local var="$1" path="$2" danger="${3:-}" reply=""

  if ! exists "$path"; then
    printf '  %-24s does not exist, will be written\n' "kv/${path}"
    eval "$var=1"
    return
  fi

  # A danger path is gated even under FORCE=1. Rotating a Route53 key with
  # FORCE=1 is a reasonable thing to want; taking Authentik down as a side
  # effect of it is not, and an env var set once in a shell is far too quiet a
  # way to authorise that. Without a terminal it is left alone rather than
  # failing, so automation still gets the others.
  if [ -n "$danger" ]; then
    if [ ! -t 0 ]; then
      printf '  %-24s exists, left alone -- needs a terminal to confirm\n' "kv/${path}"
      eval "$var=0"
      return
    fi
    printf '\n  kv/%s already exists.\n\n%s\n' "$path" "$danger"
    printf '  Type OVERWRITE to rewrite it, anything else to keep it: '
    read -r reply || reply=""
    if [ "$reply" = "OVERWRITE" ]; then
      printf '  %-24s will be overwritten\n\n' "kv/${path}"
      eval "$var=1"
    else
      printf '  %-24s left alone\n\n' "kv/${path}"
      eval "$var=0"
    fi
    return
  fi

  if [ "$FORCE" = "1" ]; then
    printf '  %-24s exists, FORCE=1 will overwrite\n' "kv/${path}"
    eval "$var=1"
    return
  fi

  if [ ! -t 0 ]; then
    printf '  %-24s exists, left alone (no terminal to ask at)\n' "kv/${path}"
    eval "$var=0"
    return
  fi

  printf '  kv/%-21s exists. Overwrite? [y/N]: ' "$path"
  read -r reply || reply=""
  case "$reply" in
    y | Y | yes | YES)
      printf '  %-24s will be overwritten\n' "kv/${path}"
      eval "$var=1"
      ;;
    *)
      printf '  %-24s left alone\n' "kv/${path}"
      eval "$var=0"
      ;;
  esac
}

AUTHENTIK_DANGER="  Rewriting it rotates Authentik's Postgres password while Postgres is
  still using the old one, and invalidates the OIDC client secrets ArgoCD
  and Grafana authenticate with. On a running cluster that is an outage."

NEXTCLOUD_DANGER="  Rewriting it issues a new OIDC client secret. Authentik and Nextcloud
  read it from here through two different ExternalSecrets that refresh
  independently, so signing in with Authentik fails until both have caught
  up. The admin password changes too -- and Nextcloud only reads that when
  it first installs, so on a running cluster the new value is written down
  and the old one is still what logs you in."

echo "### Existing paths"
decide WRITE_CERT_MANAGER cert-manager/route53
decide WRITE_EXTERNAL_DNS external-dns/route53
decide WRITE_AUTHENTIK    authentik/config "$AUTHENTIK_DANGER"
decide WRITE_MONITORING   monitoring/smtp
decide WRITE_GRAFANA      monitoring/grafana-admin
decide WRITE_NEXTCLOUD    nextcloud/config "$NEXTCLOUD_DANGER"
echo

if [ "$WRITE_CERT_MANAGER" = "0" ] && [ "$WRITE_EXTERNAL_DNS" = "0" ] \
  && [ "$WRITE_AUTHENTIK" = "0" ] && [ "$WRITE_MONITORING" = "0" ] \
  && [ "$WRITE_GRAFANA" = "0" ] && [ "$WRITE_NEXTCLOUD" = "0" ]; then
  echo "Nothing to write -- every path exists and none was chosen for overwrite."
  exit 0
fi

# --- Collect the credentials that will actually be used --------------------
#
# Only for the paths chosen above. Answering four prompts to rotate one key is
# how a hurried operator ends up pasting the wrong secret into the right path.

# Reads one value into the named variable: the environment if it is already
# set, otherwise a hidden prompt. `read -r` is what preserves a backslash;
# without it, a secret containing one arrives with it silently eaten.
prompt_secret() {
  local var="$1" description="$2" value=""

  if [ -n "${!var:-}" ]; then
    printf '  %-24s from the environment\n' "$var"
    return
  fi

  [ -t 0 ] || {
    echo "ERROR: ${var} is not set and there is no terminal to ask at." >&2
    echo "       Set it in the environment -- in single quotes, so a # or a !" >&2
    echo "       in the value survives -- or run this from a terminal." >&2
    exit 1
  }

  while [ -z "$value" ]; do
    printf '  %s\n    %s: ' "$description" "$var" >&2
    read -rs value
    printf '\n' >&2
    [ -n "$value" ] || printf '    (empty -- try again)\n' >&2
  done

  printf '  %-24s read (%d characters)\n' "$var" "${#value}"
  eval "$var=\$value"
}

echo "### Credentials that cannot be generated"
if [ "$WRITE_CERT_MANAGER" = "1" ]; then
  prompt_secret CERT_MANAGER_KEY_ID     "Route53 IAM key for cert-manager (TXT records only)"
  prompt_secret CERT_MANAGER_SECRET_KEY "  ...and its secret access key"
fi
if [ "$WRITE_EXTERNAL_DNS" = "1" ]; then
  prompt_secret EXTERNAL_DNS_KEY_ID     "Route53 IAM key for external-dns (A and TXT records)"
  prompt_secret EXTERNAL_DNS_SECRET_KEY "  ...and its secret access key"
fi
if [ "$WRITE_MONITORING" = "1" ]; then
  prompt_secret SMTP_USERNAME           "SMTP login for Alertmanager, also the sender address"
  prompt_secret SMTP_PASSWORD           "  ...and its password"
  prompt_secret SMTP_TO                 "Address alerts are delivered to"
fi
echo

# --- Write -----------------------------------------------------------------

# Values reach the pod as JSON on stdin rather than as arguments, so they stay
# out of both this shell's history and the pod's process list.
#
# The JSON is built into a variable before anything is piped. `kubectl exec -i`
# reads stdin once, as the remote command starts: a producer that is not ready
# by then -- `uv run python` starting an interpreter is easily slow enough --
# hands the pod an empty stream. A pipeline starting with `uv run` loses the
# secret that way, and reports success while doing it; a `printf` of a string
# that already exists has nothing to be late with.
put() {
  local path="$1" json
  shift
  json="$(uv run python -c '
import json, sys
print(json.dumps(dict(pair.split("=", 1) for pair in sys.argv[1:])))
' "$@")"
  printf '%s' "$json" | bao_in kv put -mount=kv "$path" - >/dev/null
  printf '  %-24s written\n' "kv/${path}"
}

rand_b64() { openssl rand -base64 "$1" | tr -d '\n'; }
rand_hex() { openssl rand -hex "$1"; }

if [ "$WRITE_CERT_MANAGER" = "1" ]; then
  put cert-manager/route53 \
    "access-key-id=${CERT_MANAGER_KEY_ID}" \
    "secret-access-key=${CERT_MANAGER_SECRET_KEY}"
fi

if [ "$WRITE_EXTERNAL_DNS" = "1" ]; then
  put external-dns/route53 \
    "access-key-id=${EXTERNAL_DNS_KEY_ID}" \
    "secret-access-key=${EXTERNAL_DNS_SECRET_KEY}"
fi

if [ "$WRITE_AUTHENTIK" = "1" ]; then
  put authentik/config \
    "secret-key=$(rand_b64 60)" \
    "postgres-password=$(rand_b64 32)" \
    "bootstrap-password=$(rand_b64 24)" \
    "bootstrap-token=$(rand_hex 32)" \
    "argocd-client-id=$(rand_hex 16)" \
    "argocd-client-secret=$(rand_b64 48)" \
    "grafana-client-id=$(rand_hex 16)" \
    "grafana-client-secret=$(rand_b64 48)"
fi

if [ "$WRITE_MONITORING" = "1" ]; then
  put monitoring/smtp \
    "username=${SMTP_USERNAME}" \
    "password=${SMTP_PASSWORD}" \
    "to=${SMTP_TO}"
fi

# Grafana reads this only when it creates its database, so rewriting it on a
# running cluster changes nothing until the password is reset to match -- see
# payload/platform/monitoring/grafana-admin.yaml.
if [ "$WRITE_GRAFANA" = "1" ]; then
  put monitoring/grafana-admin \
    "password=$(rand_b64 24)"
fi

# Four values, two consumers. Nextcloud reads all of them; Authentik reads the
# two oidc-* keys to configure the provider Nextcloud then authenticates
# against, so neither side is ever copied out of a UI. The admin account is
# break-glass only -- day-to-day logins go through Authentik.
if [ "$WRITE_NEXTCLOUD" = "1" ]; then
  put nextcloud/config \
    "username=admin" \
    "password=$(rand_b64 24)" \
    "oidc-client-id=$(rand_hex 16)" \
    "oidc-client-secret=$(rand_b64 48)"
fi

cat <<'EOF'

Done. External Secrets refreshes hourly on its own. A path that was just
rewritten is not live until it does, and cert-manager or Authentik will go on
failing with the old value until then -- so to push it through now:

  kubectl annotate externalsecret <name> -n <namespace> \
    force-sync="$(date +%s)" --overwrite

Then confirm the Secret actually changed before chasing anything downstream.
The hash moves when the contents do, and shows nothing secret:

  kubectl get secret <name> -n <namespace> \
    -o jsonpath='{.metadata.annotations.reconcile\.external-secrets\.io/data-hash}{"\n"}'

  kubectl get externalsecret -A
  kubectl get clustersecretstore openbao -o jsonpath='{.status.conditions}'

The Authentik admin password is generated, never printed, and is what you log
in with as akadmin:

  kubectl -n openbao exec openbao-0 -- bao kv get -mount=kv \
    -field=bootstrap-password authentik/config

Grafana's break-glass admin password is generated the same way:

  kubectl -n openbao exec openbao-0 -- bao kv get -mount=kv \
    -field=password monitoring/grafana-admin

So is Nextcloud's, which is the break-glass local account behind
https://cloud.k8s.wlkr.ch/login?direct=1 -- the normal login is Authentik:

  kubectl -n openbao exec openbao-0 -- bao kv get -mount=kv \
    -field=password nextcloud/config
EOF
