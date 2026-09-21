---
description: "How to read the Trivy Operator findings, when an upstream issue is warranted, and which pod-level findings are load-bearing by design."
---

# Vulnerability Triage

Trivy Operator writes one `VulnerabilityReport` per container, a few thousand
findings on a cluster this size. Almost none warrant action, and the work is in
proving that quickly. The operator's configuration and failure modes are in
[Trivy Operator](../platform/trivy-operator.md). This page is the method; the
dispositions from the last pass are in [Triage 2026-09-20](triage-2026-09-20.md).

## Pulling a report

```bash
kubectl get vulnerabilityreports -A -o json > vr.json
jq -r '.items[] | .metadata.namespace as $ns
  | .report.artifact.repository as $img
  | .report.vulnerabilities[]?
  | select(.severity=="CRITICAL")
  | [$ns, $img, .vulnerabilityID, .resource, .installedVersion,
     (.fixedVersion // "NO-FIX"), (.packagePath // .target)]
  | @tsv' vr.json | sort -u
```

## Reading it

- **A missing report is not a clean image.** A failed scan writes nothing;
    the `TrivyContainerNotScanned` alert reconciles running images against
    scanned ones continuously.
- **An empty `fixedVersion` means no fix is known, not no problem.** Unfixed
    findings are kept (`ignoreUnfixed: false`) because a cluster of them in one
    base image says the base image is the wrong one.
- **The worst findings are usually not the project's code.** The `target`
    names the binary, and which binary decides whether anything can be done and
    by whom.

Two artifacts recur. A `ConfigAuditReport` has no TTL and goes only when its
ReplicaSet is garbage-collected, so superseded revisions keep their findings on
the dashboard
([aquasecurity/trivy-operator#3069](https://github.com/aquasecurity/trivy-operator/issues/3069));
when a count looks wrong, check whether the ReplicaSet in the report name still
has replicas. `KSV-0125` ("untrusted registry") fires on nearly every
container because the check's built-in list is Azure, ECR and GCR, which Trivy
Operator does not expose as a parameter; it says nothing about the images.

## Filing policy

Filing goes out under a real name, permanently, and most projects have said
what they want:

- **Argo CD** asks in `SECURITY.md` not to raise scanner findings.
- **Home Assistant** (Open Home Foundation) does not accept issues or PRs from
    autonomous agents.
- **Rook** accepts AI-assisted contributions with disclosure, submitted by a
    human.
- **Routine Go churn is not a finding.** `grpc` or the stdlib one patch behind
    is fixed by the next release every project here takes automatically.

"Waiting on release" means the fix is merged upstream and Renovate takes the
release when it exists; nothing here speeds it up.

## What is load-bearing

`ConfigAuditReport`, `RbacAssessmentReport` and `InfraAssessmentReport` are
fixable here rather than upstream. Two checks are most of the volume:
`KSV-0014` (read-only root filesystem) and `KSV-0118` (empty pod-level
`securityContext`, whatever the containers set).

| Where | Why it stays |
| --- | --- |
| Cilium, cilium-envoy, kube-vip, the kubeadm static pods, Rook OSDs and mons, node-exporter, Kured | Host network, host PID, privileged and added capabilities are what these do; `pod-security.yaml` enforces `privileged` in those namespaces for this reason |
| `etcd-backup` CronJob | Host network, because etcd listens on the node's loopback; root, because the client certificates are `600 root` |
| Velero node agent | Root and capabilities stay: kopia reads every pod volume, whoever owns the files |
| Images that start as root by design (Nextcloud, Home Assistant) | `runAsNonRoot` or a read-only root would change how they run; seccomp is set |
| Read-only root on charts that write under `/` at runtime (Grafana sidecars, OpenBao) | The chart or image decides where; guessing costs an outage (#663) |
| Every critical or high `ClusterRole` | A chart's operator role, or Kubernetes' own `admin`, `edit` and `cluster-admin`; not narrowable without forking a chart — see [Security Posture](../architecture/security.md#authorization) |
| Node file modes kubeadm resets | Handled at provisioning time — see [Triage 2026-09-20](triage-2026-09-20.md#infra-the-nodes) |
