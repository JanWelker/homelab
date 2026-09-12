#!/usr/bin/env bash
# Destroys the Ceph OSD sitting on each node's rook-osd partition, so Rook can
# build a new one there.
#
# This is for exactly one situation: nodes that have been provisioned before,
# whose rook-osd partition still holds the previous cluster's BlueStore OSD.
# Rook will not adopt an OSD from a cluster it does not know, so it takes no
# disk at all and the cluster has nowhere to store data. See
# docs/platform/rook-ceph.md#no-osds-after-reprovisioning.
#
#   make wipe-osd                       # every host in k8s_nodes
#   make wipe-osd LIMIT=odin,thor       # only these
#
# It asks before it does anything, and there is no flag to skip the question:
# whatever is on those partitions does not come back.
set -euo pipefail

INVENTORY="${INVENTORY:-ansible/inventory.yaml}"
PATTERN="${PATTERN:-k8s_nodes}"
LIMIT="${LIMIT:-}"
PARTITION="/dev/disk/by-partlabel/rook-osd"

limit_args=()
[ -n "$LIMIT" ] && limit_args=(--limit "$LIMIT")

# -m raw throughout: Flatcar ships no Python, so every other module fails with
# rc=127. The same reason ansible/playbooks/kubeconfig.yaml uses raw.
hosts="$(uv run ansible -i "$INVENTORY" "$PATTERN" ${limit_args[@]+"${limit_args[@]}"} --list-hosts 2>/dev/null \
  | tail -n +2 | tr -d ' ' | paste -sd' ' - || true)"
[ -n "$hosts" ] || { echo "ERROR: no hosts matched ${PATTERN}${LIMIT:+ (limit ${LIMIT})}" >&2; exit 1; }

cat <<EOF

This will destroy the Ceph OSD on ${PARTITION} on:

  ${hosts}

Every object the previous cluster stored there is gone afterwards, and it is
gone either way: an OSD whose mons left with the old control plane cannot be
re-adopted by anything. If the data still matters, stop and copy it off first.

EOF

[ -t 0 ] || { echo "ERROR: refusing to wipe without a terminal to confirm at" >&2; exit 1; }
printf 'Type WIPE to continue: '
read -r answer
[ "$answer" = "WIPE" ] || { echo "Aborted; nothing was touched."; exit 1; }

echo
echo "Wiping ${PARTITION}..."
uv run ansible -i "$INVENTORY" "$PATTERN" ${limit_args[@]+"${limit_args[@]}"} --become -m raw -a \
  "wipefs -a ${PARTITION} && dd if=/dev/zero of=${PARTITION} bs=1M count=200 oflag=direct,dsync"

echo
echo "Restarting the Rook operator so it re-runs the OSD prepare jobs..."
kubectl -n rook-ceph rollout restart deploy/rook-ceph-operator
kubectl -n rook-ceph rollout status deploy/rook-ceph-operator --timeout=120s

cat <<'EOF'

The prepare jobs run again now, one OSD per node. Watch them appear:

  kubectl -n rook-ceph get pods -l app=rook-ceph-osd -w

and confirm the result end to end:

  make storage-check
EOF
