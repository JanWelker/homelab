#!/usr/bin/env bash
# Answers one question: can Rook-Ceph serve a PersistentVolume right now?
#
# Run it after the GitOps handover and before anything that wants a volume -
# the ArgoCD sync waves do not answer this. A CephCluster reports Ready with
# mons and mgrs up while having no OSDs to store data on and no CSI driver to
# hand out volumes, so the apps at later waves start anyway and sit in
# Pending. This walks the chain from the disks to a bound PVC instead, and
# names what to fix at the first link that is missing.
#
#   make storage-check
#
# Environment:
#   NAMESPACE   Rook's namespace (default rook-ceph)
#   CLASS       StorageClass to provision from (default rook-ceph-block)
#   TIMEOUT     Seconds to wait for the scratch PVC to bind (default 120)
set -euo pipefail

NAMESPACE="${NAMESPACE:-rook-ceph}"
CLASS="${CLASS:-rook-ceph-block}"
TIMEOUT="${TIMEOUT:-120}"
PVC="storage-check"
DRIVER="${NAMESPACE}.rbd.csi.ceph.com"

pass() { printf '  ok    %s\n' "$1"; }
fail() {
  printf '  FAIL  %s\n' "$1" >&2
  shift
  for line in "$@"; do printf '        %s\n' "$line" >&2; done
  exit 1
}

echo "Checking that ${CLASS} can serve volumes..."

# 1. The cluster itself. Ready only means the operator finished reconciling.
phase="$(kubectl -n "$NAMESPACE" get cephcluster -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)"
[ -n "$phase" ] || fail "no CephCluster in namespace ${NAMESPACE}" \
  "The rook-ceph-cluster Application has not synced yet. Watch it with:" \
  "  kubectl -n argocd get applications -w"
[ "$phase" = "Ready" ] || fail "CephCluster phase is ${phase}, not Ready" \
  "kubectl -n ${NAMESPACE} describe cephcluster"
pass "CephCluster is Ready"

# 2. OSDs. No OSDs is the failure a reprovisioned node produces, and the one
#    the CephCluster phase hides most convincingly.
osds="$(kubectl -n "$NAMESPACE" get pods -l app=rook-ceph-osd \
  -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null | grep -c '^Running$' || true)"
[ "${osds:-0}" -gt 0 ] || fail "no OSD pods running" \
  "The disks are there but Rook took none of them. On nodes that held a" \
  "previous Ceph cluster the rook-osd partition still carries its OSD, which" \
  "Rook will not adopt:" \
  "  kubectl -n ${NAMESPACE} logs job/rook-ceph-osd-prepare-<node> | tail" \
  "See docs/platform/rook-ceph.md#no-osds-after-reprovisioning, then:" \
  "  make wipe-osd"
pass "${osds} OSD pod(s) running"

# 3. Ceph's own opinion. HEALTH_WARN is survivable and common on a fresh
#    cluster; HEALTH_ERR is not.
if kubectl -n "$NAMESPACE" get deploy rook-ceph-tools >/dev/null 2>&1; then
  health="$(kubectl -n "$NAMESPACE" exec deploy/rook-ceph-tools -- ceph health 2>/dev/null | head -1 || true)"
  case "$health" in
    HEALTH_ERR*) fail "ceph health is ${health}" \
      "kubectl -n ${NAMESPACE} exec deploy/rook-ceph-tools -- ceph -s" ;;
    "") pass "ceph health unavailable (toolbox not answering), continuing" ;;
    *) pass "ceph health: ${health}" ;;
  esac
fi

# 4. The CSI driver. Since rook-ceph v1.20 the chart deploys none of this
#    without a Driver CR, and a StorageClass whose provisioner nothing
#    registered fails silently: PVCs just wait.
kubectl get csidriver "$DRIVER" >/dev/null 2>&1 || fail "no CSI driver registered for ${DRIVER}" \
  "The ceph-csi-operator deploys nothing without a Driver CR. Check that" \
  "payload/platform/rook-ceph/csi-driver.yaml has synced:" \
  "  kubectl -n ${NAMESPACE} get drivers.csi.ceph.io,operatorconfigs.csi.ceph.io"
pass "CSI driver ${DRIVER} is registered"

ready="$(kubectl -n "$NAMESPACE" get ds "${DRIVER}-nodeplugin" \
  -o jsonpath='{.status.numberReady}' 2>/dev/null || true)"
[ "${ready:-0}" -gt 0 ] || fail "no node plugin pods ready" \
  "kubectl -n ${NAMESPACE} describe ds ${DRIVER}-nodeplugin" \
  "A missing ServiceAccount stops the DaemonSet from creating pods at all."
pass "${ready} node plugin pod(s) ready"

kubectl -n "$NAMESPACE" get deploy "${DRIVER}-ctrlplugin" \
  -o jsonpath='{.status.availableReplicas}' 2>/dev/null | grep -q '[1-9]' \
  || fail "the provisioner is not available" \
    "kubectl -n ${NAMESPACE} describe deploy ${DRIVER}-ctrlplugin"
pass "provisioner is available"

# 5. The only check that proves the rest: ask for a volume.
cleanup() { kubectl -n "$NAMESPACE" delete pvc "$PVC" --ignore-not-found --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
kubectl apply -f - >/dev/null <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${PVC}
  namespace: ${NAMESPACE}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${CLASS}
  resources:
    requests:
      storage: 1Gi
EOF
if ! kubectl -n "$NAMESPACE" wait --for=jsonpath='{.status.phase}'=Bound \
      "pvc/${PVC}" --timeout="${TIMEOUT}s" >/dev/null 2>&1; then
  kubectl -n "$NAMESPACE" describe "pvc/${PVC}" | sed -n '/Events/,$p' >&2 || true
  fail "a ${CLASS} PVC did not bind within ${TIMEOUT}s" \
    "kubectl -n ${NAMESPACE} logs deploy/${DRIVER}-ctrlplugin -c csi-provisioner"
fi
pass "a 1Gi PVC bound and was cleaned up"

echo "Storage is ready to serve volumes."
