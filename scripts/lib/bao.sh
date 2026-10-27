# shellcheck shell=bash
# Shared by the bao-*.sh scripts and claude-session-pat.sh: reaching OpenBao
# through the pod, logging in as the operator, and writing a path. Sourced,
# never run.

NAMESPACE="${NAMESPACE:-openbao}"
POD="${POD:-openbao-0}"
KEYFILE="${KEYFILE:-output/credentials/openbao-init.json}"
# The userpass login `make bao-init` (or `make bao-rotate-root`) creates in
# place of the root token. See docs/platform/openbao.md#operator-credential.
OPERATOR_FILE="${OPERATOR_FILE:-output/credentials/openbao-operator.json}"
OPERATOR_USER="${OPERATOR_USER:-operator}"
OPERATOR_POLICY="${OPERATOR_POLICY:-operator}"

# Which token the pod has cached: operator, root or empty. Only the operator's
# is revoked on exit; revoking the root token by accident would lock out a
# cluster that has no operator credential yet.
BAO_SESSION=""

bao() { kubectl -n "$NAMESPACE" exec "$POD" -- bao "$@"; }
bao_in() { kubectl -n "$NAMESPACE" exec -i "$POD" -- bao "$@"; }

json_get() {
  uv run python -c '
import json, sys
with open(sys.argv[1]) as handle:
    print(json.load(handle).get(sys.argv[2], ""))
' "$1" "$2"
}

bao_require_unsealed() {
  local sealed
  sealed="$(kubectl -n "$NAMESPACE" exec "$POD" -- bao status -format=json 2>/dev/null \
    | uv run python -c 'import json,sys; print(json.load(sys.stdin).get("sealed",""))' 2>/dev/null || true)"
  [ "$sealed" = "False" ] || {
    echo "ERROR: ${POD} is sealed. Run 'make bao-unseal' first." >&2
    exit 1
  }
}

# The operator's token lives at most an hour (its token_ttl), so one left
# behind by a dropped connection expires on its own; the EXIT trap revokes it
# sooner.
bao_cleanup() {
  if [ "$BAO_SESSION" = "operator" ]; then
    bao token revoke -self >/dev/null 2>&1 || true
  fi
  # shellcheck disable=SC2016 # $HOME is the pod's
  kubectl -n "$NAMESPACE" exec "$POD" -- sh -c 'rm -f "$HOME/.bao-token"' >/dev/null 2>&1 || true
  BAO_SESSION=""
}

bao_login_operator() {
  local password
  password="$(json_get "$OPERATOR_FILE" password)"
  [ -n "$password" ] || { echo "ERROR: no password in ${OPERATOR_FILE}" >&2; exit 1; }
  printf '%s' "$password" \
    | bao_in login -method=userpass username="$OPERATOR_USER" password=- >/dev/null
  BAO_SESSION=operator
}

bao_login_root() {
  local root_token
  root_token="$(json_get "$KEYFILE" root_token)"
  [ -n "$root_token" ] || return 1
  printf '%s' "$root_token" | bao_in login - >/dev/null 2>&1 || return 1
  BAO_SESSION=root
}

# Fails early, before anything is prompted for, when OpenBao cannot be written
# to; otherwise logs in and logs out again when the caller exits. The operator
# credential wins; the root token is only a fallback for a cluster initialised
# before it existed, until `make bao-rotate-root` revokes it.
bao_login() {
  bao_require_unsealed

  if [ -f "$OPERATOR_FILE" ]; then
    bao_login_operator
  elif [ -f "$KEYFILE" ] && bao_login_root; then
    echo "WARNING: logged in with the root token. Run 'make bao-rotate-root'." >&2
  else
    echo "ERROR: neither ${OPERATOR_FILE} nor a valid root token in ${KEYFILE}." >&2
    echo "       See docs/platform/openbao.md#generating-a-root-token" >&2
    exit 1
  fi
  trap bao_cleanup EXIT
}

# Idempotent: enables userpass, writes the policy, and (re)writes the user with
# the password from OPERATOR_FILE, generating that file first if it is missing.
# Needs a token that can write policies and auth config: root, at init or at
# rotation. The file is written before the user, so a failure in between is
# repaired by re-running.
bao_create_operator() {
  local password

  if ! bao auth list -format=json 2>/dev/null \
    | uv run python -c 'import json,sys; sys.exit(0 if "userpass/" in json.load(sys.stdin) else 1)' 2>/dev/null; then
    bao auth enable userpass >/dev/null
    echo "    userpass auth         enabled"
  else
    echo "    userpass auth         already enabled"
  fi

  # Everything make bao-secrets, make claude-session-pat and the rotation
  # runbook do on kv/, plus Raft snapshots for backups. Policies, auth methods
  # and snapshot restores need a root token from generate-root -- whose
  # endpoints need a token since OpenBao 2.6, so the operator may start and
  # feed an attempt; the unseal shares are still what completes it.
  bao_in policy write "$OPERATOR_POLICY" - <<'EOF' >/dev/null
path "kv/data/*" {
  capabilities = ["create", "read", "update", "delete"]
}
path "kv/metadata/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "sys/storage/raft/snapshot" {
  capabilities = ["read"]
}
path "sys/generate-root-token/attempt" {
  capabilities = ["read", "update", "delete"]
}
path "sys/generate-root-token/update" {
  capabilities = ["update"]
}
path "sys/decode-token" {
  capabilities = ["update"]
}
EOF
  echo "    ${OPERATOR_POLICY} policy       written (kv/, raft snapshots, generate-root)"

  if [ ! -f "$OPERATOR_FILE" ]; then
    (
      umask 077
      uv run python -c '
import json, secrets, sys
with open(sys.argv[1], "x") as handle:
    json.dump({"username": sys.argv[2], "password": secrets.token_urlsafe(32)}, handle)
    handle.write("\n")
' "$OPERATOR_FILE" "$OPERATOR_USER"
    )
    chmod 0600 "$OPERATOR_FILE"
    echo "    operator credential   generated into ${OPERATOR_FILE}"
  fi

  password="$(json_get "$OPERATOR_FILE" password)"
  printf '%s' "$password" | bao_in write "auth/userpass/users/${OPERATOR_USER}" \
    password=- token_policies="$OPERATOR_POLICY" token_ttl=1h token_max_ttl=1h >/dev/null
  echo "    ${OPERATOR_USER} user         written (token TTL 1h)"
}

# Logs in as the operator and checks the capabilities the scripts rely on.
# Returns non-zero, without exiting, so the caller can keep the root token.
bao_verify_operator() {
  local caps
  bao_login_operator || return 1
  caps="$(bao token capabilities kv/data/operator-check 2>/dev/null || true)"
  case "$caps" in *create*) ;; *) return 1 ;; esac
  caps="$(bao token capabilities kv/metadata/operator-check 2>/dev/null || true)"
  case "$caps" in *delete*) ;; *) return 1 ;; esac
}

# Revokes the root token cached in the pod. Its children go with it; the
# operator's and ESO's tokens come from auth methods and are not children.
bao_revoke_root() {
  bao_login_root || return 1
  bao token revoke -self >/dev/null
  BAO_SESSION=""
  bao_cleanup
}

exists() {
  bao kv get -mount=kv "$1" >/dev/null 2>&1
}

# Values travel on stdin at both ends: NUL-separated into the local interpreter
# that builds the JSON, and as JSON into the pod. Neither process list shows
# them, and this shell's history never sees them.
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
  json="$(printf '%s\0' "$@" | uv run python -c '
import json, sys
pairs = sys.stdin.buffer.read().split(b"\0")[:-1]
print(json.dumps(dict(p.decode().split("=", 1) for p in pairs)))
')"
  printf '%s' "$json" | bao_in kv put -mount=kv "$path" - >/dev/null
  printf '  %-24s written\n' "kv/${path}"
}
