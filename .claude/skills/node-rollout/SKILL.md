---
name: node-rollout
description: Roll a change, a reboot or an enforcement flip across the cluster's nodes one at a time without losing quorum, Ceph redundancy or a database primary. Use when the user says "start enforcing on one worker", "continue with freya", "do the rest of the workers", "then have kured do the controlled reboot", "do the control planes over ssh", "<node> is back, should we reboot it", "should I uncordon it", "the kured wait time seems excessive", "a drain is hanging", or asks to apply a node-level change (host firewall, sysext, Ignition edit, kernel setting) to all nodes. Covers the order, the per-node loop, Kured versus a hand reboot, the checks between nodes and the pitfalls that took a node down before.
---

# node-rollout

The per-node commands are in `docs/operations/nodes.md#rebooting-a-node`
and Kured's lock and sentinel in `docs/platform/kured.md#usage`; this skill
is the order and the gates between nodes. Changes to node configuration go
in `ansible/` or `payload/` first, per the declarative rule; the loop below
only decides when each node picks them up.

## 1. Order

1. One worker as the canary. Watch it for a while (a day for an enforcement
   flip, until the next Hubble/alert pass for a policy).
2. The remaining workers, one at a time.
3. The control planes last, one at a time, over SSH (`ssh odin`, `thor`,
   `loki`; aliases from `~/.ssh/config`, never `core@IP`).

Never two nodes in flight: etcd has three members and Ceph's replica count is
three, so a second node down while the first recovers costs quorum or a
placement group.

## 2. Per node

1. Gate first (all must hold):
    - `kubectl get nodes`: every other node `Ready`, nothing cordoned.
    - `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status`:
      `HEALTH_OK`, no backfill.
    - Control planes: `etcd` members all healthy.
    - `kubectl get cluster -A` (CNPG): every database healthy; note which
      primaries sit on the node.
2. Apply the change for that node (a label for the host-firewall selector,
   an Ansible run with `--limit`, `make reinstall` for config Ignition only
   applies at provisioning).
3. Reboot when the change needs it. Prefer Kured
   (`ssh <node> sudo touch /run/reboot-required`), which drains, waits for its
   gates (Ceph, etcd, `VeleroBackupInProgress`) and uncordons. Reboot by hand
   only when the user wants it now: the steps in `nodes.md`.
4. After: node `Ready`, pods back, Ceph `HEALTH_OK`, OpenBao unsealed if a
   replica ran there (`openbao-ops`), the change verifiable on the node
   (e.g. `cilium-dbg` policy state, `ss -ltnp`, `systemd-sysext list`).
5. Tell the user which node is next and wait if they set the pace.

## 3. Pitfalls

- A drain looping on a PDB for `<name>-db-<n>`: a single-instance CNPG
  cluster with `enablePDB` still on. Replicated clusters keep their PDB so the
  drain switches the primary over; see
  `docs/platform/cloudnative-pg.md#the-contract`.
- A switchover that is slow on a plain drain: check `switchoverDelay` and
  `smartShutdownTimeout`, not the network.
- Flatcar's `/etc` is an overlay: never `rm` an Ignition-shipped mask in
  `/etc/extensions` on a running node. Replace it with the shipped link
  (valkyrie lost containerd this way on 2026-09-28).
- A reboot does not reprovision: hand edits survive until `make reinstall`.
- A node that does not come back after Kured: `kubectl cordon` it so nothing
  schedules there, then debug; do not let Kured move to the next node
  (take its lock: `docs/platform/kured.md#usage`).
- Prometheus on a node being drained can OOM on restart; size its memory
  from history rather than restart it in a loop.
- Kured timings (`period`, drain timeout, the wait) live in
  `payload/platform/kured/application.yaml`; tune them from past reboot
  durations in its logs, not by guess.

## 4. Report

A table per node: change applied, rebooted (Kured or hand), checks passed,
time. Then what is left and which node is next.
