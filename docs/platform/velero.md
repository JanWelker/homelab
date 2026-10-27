---
description: "Velero backs up Kubernetes objects and PVC data nightly into the Ceph object store; the chart values that make it work against RGW."
---

# Velero

[Velero](https://velero.io/) backs up Kubernetes objects and, through CSI
snapshots and its data mover, the data in every PVC, nightly into a bucket in
the Ceph object store. What it covers, what it does not, and the restore
runbooks are in [Backups & Recovery](../operations/backups.md); this page is
the chart tuning.

## At a glance

| | |
| --- | --- |
| Namespace | `backup` |
| Depends on | [Rook-Ceph](rook-ceph.md) for the bucket claims and RBD snapshots |
| If it is down | Nightly backups stop; `VeleroBackupTooOld` fires after 36 hours |
| Health check | `kubectl -n backup get backupstoragelocation` reads `Available` |
| Files | `payload/platform/velero/`, `payload/platform/backup/`, `payload/platform/snapshot-controller/` |

## Configuration

| Setting | Why |
| --- | --- |
| `checksumAlgorithm: ""` on the `BackupStorageLocation` | The AWS plugin's SDK sends a trailing checksum that Ceph RGW rejects with `XAmzContentSHA256Mismatch`; every upload fails without it |
| `s3Url: https://s3.infra.k8s.wlkr.ch`, not the RGW Service | kopia's S3 client signs plain-HTTP uploads with the streaming SigV4 scheme, which Ceph 20.2.4 rejects as unsigned headers ([CVE-2026-54330](https://docs.ceph.com/en/latest/security/CVE-2026-54330/)); over TLS it sends `UNSIGNED-PAYLOAD` and RGW accepts it. The infra Gateway terminates TLS with the Let's Encrypt wildcard, so no CA has to reach Velero or the movers |
| `velero-plugin-for-aws` init container | The plugin minor must match the chart's Velero appVersion; the chart's commented example is one line behind |
| `credentials.useSecret: false` with `extraEnvVars` | Keys come from the environment, fed by the `Secret` Rook writes for the `velero-bucket` claim; nothing is rendered into a file or Git |
| `defaultSnapshotMoveData`, `deployNodeAgent`, `uploaderType: kopia` | A CSI snapshot is a Ceph object in the same cluster; the data mover (run by the node agent) streams it into the bucket and deletes the snapshot |
| `volumeSnapshotLocation: []` | The chart's placeholder renders a `VolumeSnapshotLocation` the CRD rejects, failing every sync; Helm replaces lists, so the empty list removes it |
| `VolumeSnapshotClass` in `payload/platform/backup/volumesnapshotclass.yaml` | The `velero.io/csi-volumesnapshot-class` label is how Velero finds the class; without it volumes are skipped silently. `deletionPolicy: Delete`, because the durable copy is the data mover's |
| `snapshot-controller` Application | Neither kubeadm nor Rook installs the CSI snapshot controller or its CRDs, which must exist before the `VolumeSnapshotClass` applies, so it is its own Application. It applies the two kustomize directories [kubernetes-csi/external-snapshotter](https://github.com/kubernetes-csi/external-snapshotter) publishes at each tag, the owning project's only packaging, namespaced into `backup` with the hardening as a patch; the Deployment at a tag runs the previous release's image, which is how upstream cuts releases |
| No `runAsNonRoot` on the `velero` container | Whether the plugin init container's copy into `/target` works non-root depends on the image's `USER`; confirm on a real backup first |
| Node agent `containerSecurityContext` | Root and capabilities stay because kopia reads every pod volume; escalation is off and seccomp is on |
| `prometheusRule` | `VeleroBackupFailures` and `VeleroBackupTooOld` (critical), `VeleroBackupPartialFailures` (warning); only the age alert catches a backup that hangs `InProgress` |

### Repository password

Kopia encrypts the volume data in every repository with one password, read
from the Secret `velero-repo-credentials` (key `repository-password`) in
Velero's namespace. When that Secret is missing Velero creates it with a
default compiled into the binary, which protects nothing, so
`payload/platform/backup/velero-repo-credentials.yaml` supplies it.

| Setting | Why |
| --- | --- |
| A `Password` generator and `PushSecret` writing `velero/repository` | A [generated secret](openbao.md#generated-secrets): random, written once, and in the OpenBao snapshot that a restore on a fresh cluster needs anyway |
| `creationPolicy: Owner` on the `ExternalSecret` | ESO takes over the Secret Velero created at start-up and overwrites the default |
| `velero.io/exclude-from-backup` on the Secret | The resource backup lands in the same bucket as the repositories the password unlocks |
| Secrets stay in the resource backup | The tarball under `backups/` is not encrypted, so every backed-up `Secret` sits in the bucket as JSON. Excluding them would break a namespace restore: CloudNativePG, the bucket claims, cert-manager and Argo CD generate Secrets that exist nowhere else. The `velero` bucket and its keys are therefore as sensitive as an unencrypted etcd |

A repository keeps the password it was initialised with, and all
repositories share one, so a changed value makes every existing repository
unreachable; see [Re-initialising the repositories](#re-initialising-the-repositories).

## Usage

```bash
velero -n backup backup create manual-$(date +%s) --include-namespaces my-app
```

## Health check

```bash
kubectl -n backup get backupstoragelocation       # Available
kubectl -n backup get backups.velero.io           # Completed, not PartiallyFailed
kubectl -n backup get volumesnapshotclass rook-ceph-block
kubectl -n backup get backuprepositories.velero.io  # Ready
kubectl -n backup get externalsecret velero-repo-credentials   # SecretSynced
```

## Pitfalls

!!! warning "PartiallyFailed with no volume data"
    Velero found no labelled `VolumeSnapshotClass` and skipped every volume without an error. Check the class above exists and carries the label.

!!! warning "Every `BackupRepository` NotReady, `Access Denied` on `kopia.blobcfg`"
    Objects upload, volume data does not: the RGW log shows `PUT /velero/kopia/...` from `minio-go` with no authenticated user while the AWS SDK's PUTs succeed. The S3 URL is plain HTTP again; see the `s3Url` row above.

!!! warning "Every `BackupRepository` NotReady, `invalid repository password`"
    The password changed under existing repositories: OpenBao was rebuilt without its snapshot, `velero/repository` was deleted, or a backup ran on a fresh cluster before OpenBao was unsealed and initialised them with Velero's default. Volume data stops and backups end `PartiallyFailed`. Restore the old value into OpenBao if it exists anywhere; otherwise re-initialise as below.

## Recovery

### Re-initialising the repositories

Discards every backup in the bucket, resource backups included: a `Backup`
whose volume snapshots cannot be reached never finishes deleting, so the
bucket is emptied as a whole and Velero drops the `Backup` objects it no
longer finds there.

1. Confirm the Secret carries the value from OpenBao, not Velero's default.

    ```bash
    kubectl -n backup get externalsecret velero-repo-credentials   # SecretSynced
    ```

2. Delete the repository objects.

    ```bash
    kubectl -n backup delete backuprepositories.velero.io --all
    ```

3. Empty the bucket with its own keys, from a machine that reaches the infra Gateway.

    ```bash
    export AWS_ACCESS_KEY_ID=$(kubectl -n backup get secret velero-bucket -o jsonpath='{.data.AWS_ACCESS_KEY_ID}' | base64 -d)
    export AWS_SECRET_ACCESS_KEY=$(kubectl -n backup get secret velero-bucket -o jsonpath='{.data.AWS_SECRET_ACCESS_KEY}' | base64 -d)
    export AWS_CONFIG_FILE=$(mktemp)    # path-style addressing, without touching ~/.aws
    printf '[default]\ns3 =\n    addressing_style = path\n' > "$AWS_CONFIG_FILE"
    aws --endpoint-url https://s3.infra.k8s.wlkr.ch --region eu-central-1 s3 rm s3://velero --recursive
    ```

4. Restart Velero and the node agents, so no cached repository connection survives.

    ```bash
    kubectl -n backup rollout restart deployment/velero daemonset/node-agent
    ```

5. Take a backup now rather than at the next schedule, and confirm it.

    ```bash
    velero -n backup backup create --from-schedule velero-daily
    kubectl -n backup get backups.velero.io               # Completed
    kubectl -n backup get backuprepositories.velero.io    # Ready
    ```
