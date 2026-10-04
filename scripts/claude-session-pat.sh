#!/usr/bin/env bash
# Stores, or removes, the GitHub PAT of one in-cluster Claude Code session.
#
#   make claude-session-pat SESSION=homelab
#   make claude-session-pat SESSION=homelab DELETE=1
#
# The session's ExternalSecret reads kv/claude-<session>/github (`token`), so a
# session comes and goes without a change to the platform. The PAT is read with
# hidden input, or from CLAUDE_GITHUB_TOKEN when that is set. Empty input writes
# an empty token, which is what lets the ExternalSecret resolve before a PAT
# exists. See docs/platform/openbao.md#kv-layout.
#
# Rewriting an existing token is the point of running this again, so it is not
# asked about. Removing the path asks for the session name, typed.
set -euo pipefail

usage() {
  echo "usage: claude-session-pat.sh [--delete] <session>" >&2
  exit 2
}

delete=0
if [ "${1:-}" = "--delete" ]; then
  delete=1
  shift
fi
[ "$#" -eq 1 ] || usage
session="$1"

# A DNS label: the session name ends up in the ExternalSecret's name.
if ! printf '%s' "$session" | grep -Eq '^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$'; then
  echo "ERROR: '${session}' is not a DNS label (lowercase letters, digits and '-', at most 63 characters)." >&2
  exit 2
fi

# shellcheck source=lib/bao.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/lib/bao.sh"

path="claude-${session}/github"
bao_login

if [ "$delete" = "1" ]; then
  [ -t 0 ] || {
    echo "ERROR: deleting needs a terminal to confirm at." >&2
    exit 1
  }
  exists "$path" || {
    echo "kv/${path} does not exist, nothing to delete."
    exit 0
  }
  printf '  Type the session name (%s) to delete kv/%s: ' "$session" "$path"
  read -r reply || reply=""
  [ "$reply" = "$session" ] || {
    echo "  left alone"
    exit 1
  }
  bao kv metadata delete -mount=kv "$path" >/dev/null
  printf '  %-24s deleted\n' "kv/${path}"
  exit 0
fi

# Hidden input, so the PAT is in neither the process list nor the history. `read
# -r` keeps a backslash; the environment variable is for non-interactive use.
token="${CLAUDE_GITHUB_TOKEN:-}"
if [ -n "$token" ]; then
  printf '  CLAUDE_GITHUB_TOKEN      from the environment\n'
elif [ -t 0 ]; then
  printf '  GitHub PAT for session %s (Enter until you have one): ' "$session" >&2
  read -rs token
  printf '\n' >&2
  printf '  token                    read (%d characters)\n' "${#token}"
else
  printf '  CLAUDE_GITHUB_TOKEN      not set and no terminal to ask at, left empty\n'
fi

put "$path" "token=${token}"

echo "External Secrets refreshes hourly; to pick it up now:"
echo "  kubectl -n claude-agents annotate externalsecret claude-${session}-github force-sync=\$(date +%s) --overwrite"
