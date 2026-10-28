---
description: "Rotating the key that encrypts Secrets in etcd, without restarting an API server."
---

# Encryption Key Rotation

The API servers reload `/etc/kubernetes/enc/encryption-config.yaml` whenever
it changes (`encryption-provider-config-automatic-reload` in
`ansible/templates/kubeadm.yaml.j2`), so a rotation is a file edit on each
control plane and one rewrite of every Secret; what the key protects and how it
is generated is in [Encryption at rest](../architecture/security.md#encryption-at-rest).

The first key in the list encrypts, every key in the list decrypts, and a
Secret's ciphertext names the key it was written with. Two things follow: the
new key goes in *above* the old one, and the cluster has to end up with the new
key under the name the Butane template renders (`key1` in
`ansible/templates/butane_node_config.yaml.j2`), because a control plane
provisioned later gets exactly that file and can read nothing written under
another name. That is why the Secrets are rewritten twice.

Reading or writing the file from a shell fires `TetragonSensitiveFileAccess`;
that is the alert doing its job, not a problem to fix.

## Runbook

1. Generate the new key beside the old one, in the shape `config.yaml`'s
   `password` lookup produces: 32 letters and digits, stored in plain text, and
   base64-encoded where it goes into the config.

    ```bash
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32 > output/credentials/encryption_key.new
    base64 < output/credentials/encryption_key.new    # the secret for key2 below
    ```

2. On every control plane, add the new key as `key2` **above** `key1` in
   `/etc/kubernetes/enc/encryption-config.yaml`; the rest of the file stays as
   it is.

    ```yaml
    providers:
      - secretbox:
          keys:
            - name: key2
              secret: <new key, base64>
            - name: key1
              secret: <old key, base64>
      - identity: {}
    ```

3. Wait for every API server to reload. Each reloads its own file, so ask each
   one by its `ansible_host` rather than through the VIP; a `status="failure"`
   count means the file did not parse and the old config is still in effect.

    ```bash
    kubectl get --raw /metrics --server https://<control-plane address>:6443 | grep apiserver_encryption_config_controller_automatic_reloads_total
    ```

4. Rewrite every Secret so it is encrypted with `key2`. A conflict on a Secret
   something updated mid-stream is harmless: run it again.

    ```bash
    kubectl get secrets -A -o json | kubectl replace -f -
    ```

5. On every control plane, replace `key1`'s secret with the new key and move
   it back above `key2`; both entries now hold the new key. Wait for the reload
   as in step 3.

    ```yaml
    keys:
      - name: key1
        secret: <new key, base64>
      - name: key2
        secret: <new key, base64>
    ```

6. Rewrite every Secret again, so every ciphertext names `key1`.

    ```bash
    kubectl get secrets -A -o json | kubectl replace -f -
    ```

7. On every control plane, remove `key2`. The file now matches what Ignition
   writes for a new control plane, and nothing on any node holds the old key.

    ```yaml
    keys:
      - name: key1
        secret: <new key, base64>
    ```

8. Make the new key the one `make config` renders, and verify that the rendered
   config carries it; `config.yaml` reads the file back instead of generating a
   new value, so nothing else changes.

    ```bash
    mv output/credentials/encryption_key.new output/credentials/encryption_key
    make config && grep -c "$(base64 < output/credentials/encryption_key)" output/http/ignition-*.json
    ```

9. Confirm against etcd that a Secret's ciphertext names the new key, with the
   same `etcdctl` flags as [Taking one by hand](backups.md#taking-one-by-hand);
   the value starts with `k8s:enc:secretbox:v1:key1:`.

    ```bash
    kubectl -n kube-system exec etcd-<node> -- etcdctl --cacert /etc/kubernetes/pki/etcd/ca.crt --cert /etc/kubernetes/pki/etcd/server.crt --key /etc/kubernetes/pki/etcd/server.key get /registry/secrets/kube-system/ --prefix --limit 1 | head -c 120
    ```

Store the new key with the rest of `output/credentials/` wherever the old one
is kept, and keep the old one next to the etcd snapshots taken before the
rotation: those restore only with the key they were written under.
