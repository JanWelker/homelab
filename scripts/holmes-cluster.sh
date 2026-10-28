#!/usr/bin/env bash
# Ask the in-cluster HolmesGPT, which has Prometheus, Loki, Argo CD and GitHub
# wired in and the cluster's own credentials. Nothing runs on the laptop but a
# port-forward. See docs/platform/holmesgpt.md#usage
#
#   holmes-cluster "why is the velero backup partially failed?"
#   holmes-cluster --model haiku "which pods restarted in the last hour?"
#   holmes-cluster --tools "..."        # also list the tool calls it made
set -euo pipefail

model="" tools=0
while [ $# -gt 0 ]; do
  case "$1" in
    --model) model="$2"; shift 2 ;;
    --tools) tools=1; shift ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || { echo "usage: holmes-cluster [--model sonnet|haiku] [--tools] \"question\"" >&2; exit 2; }
ask="$*"

port=$(( 20000 + RANDOM % 10000 ))
kubectl -n holmesgpt port-forward svc/holmesgpt-holmes "${port}:80" >/dev/null 2>&1 &
pf=$!
trap 'kill "$pf" 2>/dev/null' EXIT
for _ in $(seq 1 50); do
  curl -s -o /dev/null "http://localhost:${port}/healthz" && break
  sleep 0.2
done

body=$(jq -n --arg ask "$ask" --arg model "$model" \
  '{ask: $ask} + (if $model == "" then {} else {model: $model} end)')
reply=$(curl -s -m 600 "http://localhost:${port}/api/chat" \
  -H 'content-type: application/json' -d "$body")

printf '%s' "$reply" | jq -e . >/dev/null 2>&1 || { echo "holmes answered with something that is not JSON:" >&2; echo "$reply" >&2; exit 1; }
printf '%s\n' "$reply" | jq -r '.analysis // .detail // .'
if [ "$tools" = 1 ]; then
  echo; echo "--- tool calls ---"
  printf '%s\n' "$reply" | jq -r '.tool_calls[]? | "\(.tool_name): \(.description // "" | .[0:160])"'
fi
