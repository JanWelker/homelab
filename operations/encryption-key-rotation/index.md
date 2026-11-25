# Encryption Key Rotation

`encryption_keys` in `ansible/inventory.yaml` lists the keys the control planes get: the first encrypts, every one decrypts, and a Secret's ciphertext names the key it was written with. A rotation is three versions of that list, each [reprovisioned](<https://homelab.wlkr.ch/operations/nodes/index.md#reprovisioning-a-node>) onto every control plane; `make reprovision` refuses a list that drops a key one of them still encrypts with, or puts first a key one cannot decrypt yet. What the key protects is in [Encryption at rest](<https://homelab.wlkr.ch/architecture/security/index.md#encryption-at-rest>).

`make config` creates a key's file the first time its name appears: `output/credentials/encryption_key` for `key1`, `encryption_key_<name>` for any other. Reading the rendered config from a shell fires `TetragonSensitiveFileAccess`; that is the alert doing its job.

## Runbook

Each step is one commit to `encryption_keys`, then the reprovision. Kured reboots the control planes one at a time; the step is done when all three are `Ready` and `current` in `make node-drift LIMIT=control_plane`.

1. Add the new key second, so every API server can read it before any writes it.

   ```yaml
   encryption_keys: [key1, key2]
   ```

   ```bash
   make reprovision LIMIT=control_plane
   ```
2. Move it first, and reprovision the same way.

   ```yaml
   encryption_keys: [key2, key1]
   ```
3. Rewrite every Secret so it is encrypted with `key2`. A conflict on a Secret something updated mid-stream is harmless: run it again.

   ```bash
   kubectl get secrets -A -o json | kubectl replace -f -
   ```
4. Drop the old key, and reprovision the same way.

   ```yaml
   encryption_keys: [key2]
   ```
5. Confirm against etcd that a Secret's ciphertext names the new key, with the same `etcdctl` flags as [Taking one by hand](<https://homelab.wlkr.ch/operations/backups/index.md#taking-one-by-hand>); the value starts with `k8s:enc:secretbox:v1:key2:`.

   ```bash
   kubectl -n kube-system exec etcd-<node> -- etcdctl --cacert /etc/kubernetes/pki/etcd/ca.crt --cert /etc/kubernetes/pki/etcd/server.crt --key /etc/kubernetes/pki/etcd/server.key get /registry/secrets/kube-system/ --prefix --limit 1 | head -c 120
   ```

Keep the old key's file next to the etcd snapshots taken before the rotation: those restore only with the key they were written under.
