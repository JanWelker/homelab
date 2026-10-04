# shellcheck shell=bash
# Shared by bao-secrets.sh and claude-session-pat.sh: reaching OpenBao through
# the pod, logging in with the root token `make bao-init` saved, and writing a
# path. Sourced, never run.

NAMESPACE="${NAMESPACE:-openbao}"
POD="${POD:-openbao-0}"
KEYFILE="${KEYFILE:-output/credentials/openbao-init.json}"

bao() { kubectl -n "$NAMESPACE" exec "$POD" -- bao "$@"; }
bao_in() { kubectl -n "$NAMESPACE" exec -i "$POD" -- bao "$@"; }

# Fails early, before anything is prompted for, when OpenBao cannot be written
# to; otherwise logs in and removes the token from the pod when the caller exits.
bao_login() {
  local sealed root_token

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
  trap bao_cleanup EXIT
}

bao_cleanup() {
  kubectl -n "$NAMESPACE" exec "$POD" -- sh -c 'rm -f "$HOME/.bao-token"' >/dev/null 2>&1 || true
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

