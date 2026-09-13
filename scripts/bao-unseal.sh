#!/usr/bin/env bash
# Unseals every sealed OpenBao replica, using the key shares written by
# `make bao-init`.
#
# OpenBao seals itself on every pod restart -- a node reboot, a Kured cycle, a
# chart bump, a kubelet having a bad day -- and nothing unseals it for you.
# While it is sealed no ExternalSecret resolves, which eventually means
# cert-manager cannot renew a certificate. This is that chore, scripted.
#
#   make bao-unseal
#
# It reads output/credentials/openbao-init.json. If you have moved the keys
# into a password manager and deleted that file -- which is where they belong --
# this cannot help and the pods want unsealing by hand, three shares each:
#
#   kubectl -n openbao exec -it openbao-0 -- bao operator unseal
set -euo pipefail

NAMESPACE="${NAMESPACE:-openbao}"
KEYFILE="${KEYFILE:-output/credentials/openbao-init.json}"
SELECTOR="${SELECTOR:-app.kubernetes.io/name=openbao}"
# How long to wait for a replica to appear and join the raft cluster.
TIMEOUT="${TIMEOUT:-120}"

[ -f "$KEYFILE" ] || {
  echo "ERROR: ${KEYFILE} not found." >&2
  echo "       Either this cluster was never initialised (make bao-init), or the" >&2
  echo "       keys have been moved somewhere safer and unsealing is manual." >&2
  exit 1
}

# `bao status` exits 2 when sealed and 1 when it cannot reach the server, so
# every call here tolerates a non-zero exit and reads the JSON instead.
bao_status() {
  kubectl -n "$NAMESPACE" exec "$1" -- bao status -format=json 2>/dev/null || true
}

json_field() {
  uv run python -c '
import json, sys
try:
    print(json.loads(sys.stdin.read()).get(sys.argv[1], ""))
except Exception:
    print("")
' "$1"
}

# Read into an array the long way round: macOS ships bash 3.2, which has no
# mapfile, and this is the deployment host's default shell.
keys=()
while IFS= read -r key; do
  keys+=("$key")
done < <(uv run python -c '
import json, sys
with open(sys.argv[1]) as handle:
    data = json.load(handle)
keys = data.get("unseal_keys_b64") or data.get("keys_base64") or []
if not keys:
    sys.exit(f"ERROR: no unseal keys in {sys.argv[1]}")
print("\n".join(keys))
' "$KEYFILE")

[ "${#keys[@]}" -gt 0 ] || { echo "ERROR: no unseal keys read from ${KEYFILE}" >&2; exit 1; }

threshold="$(uv run python -c '
import json, sys
with open(sys.argv[1]) as handle:
    print(json.load(handle).get("unseal_threshold", 3))
' "$KEYFILE")"

pods="$(kubectl -n "$NAMESPACE" get pods -l "$SELECTOR" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | sort)"
[ -n "$pods" ] || { echo "ERROR: no pods matching ${SELECTOR} in ${NAMESPACE}" >&2; exit 1; }

sealed_any=0

for pod in $pods; do
  # A replica that has not joined the raft cluster yet reports
  # Initialized: false and turns unseal keys away, so wait it out rather than
  # failing on a pod that is still catching up with retry_join.
  deadline=$((SECONDS + TIMEOUT))
  while :; do
    status="$(bao_status "$pod")"
    initialized="$(printf '%s' "$status" | json_field initialized)"
    [ "$initialized" = "True" ] && break
    [ "$SECONDS" -lt "$deadline" ] || {
      echo "  ${pod}: still reports Initialized=false after ${TIMEOUT}s -- it has not"
      echo "           joined the raft cluster. See docs/platform/openbao.md#2-unseal-each-replica"
      continue 2
    }
    sleep 3
  done

  if [ "$(printf '%s' "$status" | json_field sealed)" != "True" ]; then
    printf '  %-12s already unsealed\n' "$pod"
    continue
  fi

  sealed_any=1
  for index in $(seq 0 $((threshold - 1))); do
    printf '%s' "${keys[$index]}" \
      | kubectl -n "$NAMESPACE" exec -i "$pod" -- bao operator unseal - >/dev/null
  done

  if [ "$(bao_status "$pod" | json_field sealed)" = "False" ]; then
    printf '  %-12s unsealed\n' "$pod"
  else
    printf '  %-12s STILL SEALED\n' "$pod"
  fi
done

[ "$sealed_any" -eq 1 ] || echo "Nothing to do."
