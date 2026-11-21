---
description: "Velero backs up Kubernetes objects and PVC data nightly into the Ceph object store; the chart values that make it work against RGW."
---

# Velero

[Velero](https://velero.io/) backs up Kubernetes objects and, through CSI
snapshots and its data mover, every PVC, nightly into its prefix of the
`offsite` bucket. Coverage and restore runbooks are in
[Backups & Recovery](../operations/backups.md); this page is the chart tuning.

## At a glance

| | |
| --- | --- |
| Namespace | `backup` |
| Depends on | [Rook-Ceph](rook-ceph.md) for its S3 user and RBD snapshots |
| If it is down | Nightly backups stop; `VeleroBackupTooOld` fires once the last success is older than the rule's threshold |
| Health check | `kubectl -n backup get backupstoragelocation` reads `Available` |
| Files | `payload/platform/velero/`, `payload/platform/backup/`, `payload/platform/snapshot-controller/` |

## Configuration

In `application.yaml`'s `valuesObject` unless a file is named:

| Setting | Why |
| --- | --- |
| `checksumAlgorithm: ""` on the `BackupStorageLocation` | The AWS plugin's SDK sends a trailing checksum Ceph RGW rejects (`XAmzContentSHA256Mismatch`) |
| `s3Url: https://s3.infra.k8s.wlkr.ch`, not the RGW Service | kopia signs plain-HTTP uploads with streaming SigV4, which Ceph RGW rejects since [CVE-2026-54330](https://docs.ceph.com/en/latest/security/CVE-2026-54330/); over TLS it sends `UNSIGNED-PAYLOAD` and RGW accepts it. The infra Gateway's Let's Encrypt wildcard means no CA has to reach Velero or the movers |
| `velero-plugin-for-aws` init container | The plugin minor must match the chart's Velero appVersion; the chart's commented example is one line behind |
| `credentials.useSecret: false` with `extraEnvVars` | Keys come from the environment, fed by the `Secret` Rook writes for the `backup.velero` [backup writer](rook-ceph.md#backup-writers); nothing is rendered into a file or Git |
| `bucket: offsite`, `prefix: backup.velero` | The writer's prefix, the only place its keys may write |
| `defaultSnapshotMoveData`, `deployNodeAgent`, `uploaderType: kopia` | A CSI snapshot is a Ceph object in the same cluster; the data mover streams it into the bucket and deletes the snapshot |
| `volumeSnapshotLocation: []` | The chart's placeholder renders a `VolumeSnapshotLocation` the CRD rejects, failing every sync; Helm replaces lists, so the empty list removes it |
| `VolumeSnapshotClass` in `payload/platform/backup/volumesnapshotclass.yaml` | The `velero.io/csi-volumesnapshot-class` label is how Velero finds the class; without it volumes are skipped silently. `deletionPolicy: Delete`, because the durable copy is the data mover's |
| `snapshot-controller` Application | Neither kubeadm nor Rook installs the CSI snapshot controller or its CRDs, which must exist before the `VolumeSnapshotClass` applies. It applies the two kustomize directories [kubernetes-csi/external-snapshotter](https://github.com/kubernetes-csi/external-snapshotter) publishes at each tag, namespaced into `backup` with the hardening as a patch and the image pinned to the tag, because upstream's Deployment at a tag still names the previous image; Renovate moves the two together, see [Maintenance](../development/maintenance.md#automerge-policy). `pdb.yaml` adds the PDB upstream lacks |
| `rbac.clusterAdministrator` at the chart default | A restore re-creates arbitrary kinds, RBAC included, and the API server refuses a role or binding granting rights the caller lacks, so only cluster-admin restores everything |
| No `runAsNonRoot` on the `velero` container | Unverified for the plugin init container's copy into `/target` |
| Node agent `containerSecurityContext` | Root and capabilities stay because kopia reads every pod volume; escalation is off and seccomp is on |
| `prometheusRule` | Four rules: `VeleroBackupFailures` (critical) and `VeleroBackupPartialFailures` (warning) on a schedule's outcome, `VeleroBackupTooOld` (critical), which alone catches a backup hanging `InProgress`, and `VeleroBackupInProgress` (info), which is [Kured](kured.md#configuration)'s reboot gate rather than a problem to mail |

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
| Secrets stay in the resource backup | The tarball under `backups/` is not encrypted, so every backed-up `Secret` sits in the bucket as JSON. Excluding them would break a namespace restore: CloudNativePG, the bucket claims, cert-manager and Argo CD generate Secrets that exist nowhere else. Velero's prefix and every key that reads it are as sensitive as an unencrypted etcd |

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
    Objects upload, volume data does not: RGW logs `PUT /offsite/backup.velero/kopia/...` from `minio-go` with no authenticated user while the AWS SDK's PUTs succeed. The S3 URL is plain HTTP again; see the `s3Url` row above.

!!! warning "Every `BackupRepository` NotReady, `invalid repository password`"
    The password changed under existing repositories: OpenBao was rebuilt without its snapshot, `velero/repository` was deleted, or a backup ran on a fresh cluster before OpenBao was unsealed and initialised them with Velero's default. Volume data stops and backups end `PartiallyFailed`. Restore the old value into OpenBao if it exists anywhere; otherwise re-initialise as below.

!!! warning "`repository not initialized in the provided storage` after a bucket or prefix change"
    Velero re-establishes only some `BackupRepository` objects when the backup location moves; the rest stay `Ready` against a prefix that holds no repository, and their volumes fail. Delete them all with `kubectl -n backup delete backuprepositories.velero.io --all`; the next backup re-creates each under the new prefix.

!!! warning "Every `BackupRepository` without a phase, `failed to wait BackupRepository`"
    The repository controller waits for each maintenance job to finish, one at a time. A job whose pod cannot start, typically `CreateContainerConfigError` after the credentials Secret was renamed, blocks every other repository behind it; `KubeContainerWaiting` fires on the job's pod. Delete it, then restart `deploy/velero`.

## Recovery

### Re-initialising the repositories

Discards every Velero backup, resource backups included: a `Backup` with
unreachable snapshots never finishes deleting, so the prefix is emptied whole.

1. Confirm the Secret carries the value from OpenBao, not Velero's default.

    ```bash
    kubectl -n backup get externalsecret velero-repo-credentials   # SecretSynced
    ```

2. Delete the repository objects.

    ```bash
    kubectl -n backup delete backuprepositories.velero.io --all
    ```

3. Empty Velero's prefix with its own keys, from a machine that reaches the infra Gateway.

    ```bash
    export AWS_ACCESS_KEY_ID=$(kubectl -n backup get secret rook-ceph-object-user-object-store-backup.velero -o jsonpath='{.data.AccessKey}' | base64 -d)
    export AWS_SECRET_ACCESS_KEY=$(kubectl -n backup get secret rook-ceph-object-user-object-store-backup.velero -o jsonpath='{.data.SecretKey}' | base64 -d)
    export AWS_CONFIG_FILE=$(mktemp)    # path-style addressing, without touching ~/.aws
    printf '[default]\ns3 =\n    addressing_style = path\n' > "$AWS_CONFIG_FILE"
    aws --endpoint-url https://s3.infra.k8s.wlkr.ch --region eu-central-1 s3 rm s3://offsite/backup.velero --recursive
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

### Removing a deleted workload

A removed namespace leaves its `BackupRepository` and its Kopia data in the
bucket, which Velero never prunes.

1. Delete the repository object and its finished maintenance Jobs.

    ```bash
    kubectl -n backup delete backuprepositories.velero.io <namespace>-default-kopia
    kubectl -n backup delete jobs -l velero.io/repo-name=<namespace>-default-kopia
    ```

2. List, then remove, `offsite/backup.velero/kopia/<namespace>/` with the keys from step 3 of [Re-initialising the repositories](#re-initialising-the-repositories).
