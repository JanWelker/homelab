---
name: selinux-denials
description: Analyse the SELinux denials the nodes log in permissive mode and turn them into the smallest change that lets a workload run under enforcing, a pod label, a host path label, a boolean or a policy module. Use when the user says "analyse the selinux denials", "what would enforcing break", "generate the selinux rules for tetragon", "which pods need spc_t", "create the se linux rules from the logs", "can we turn selinux on now", "why is there no avc in the journal", or asks to move a node to enforcing. Covers reading the denials from Loki, the grouping script, the decision per denial, building and loading a module on Flatcar, managing SELinux through Kubernetes, and the trial before any node is switched. The node-by-node switch itself is node-rollout.
---

# selinux-denials

Every node runs SELinux permissive and sends its denials to Loki
(`docs/architecture/security.md#node-hardening`). A denial there is what
enforcing would refuse. The goal is not a policy that makes the log quiet: it
is the narrowest change per workload, in the repository, that makes its
denials stop.

## 1. Read the denials

```bash
cd .claude/skills/selinux-denials
uv run python selinux_denials.py rules --since 7d                 # every rule, grouped
uv run python selinux_denials.py rules --since 7d --comm tetragon # one command
uv run python selinux_denials.py paths --since 24h --comm ceph-exporter
```

In Grafana Explore the same lines are
`{job="systemd-journal"} |= "avc:" |= "denied"`, label `node`.

Read the output with these in mind:

- **Counts are log lines, not accesses.** Permissive mode logs a denial once
  per AVC cache entry. Compare the set of rules, never the numbers.
- **A quiet workload has not been cleared.** Startup-only accesses appear when
  the pod restarts. To make running processes log again, reload the policy
  through that node's operator daemon, never with the host's tools:
  `kubectl -n security-profiles-operator exec <spod pod> -c selinuxd -- semodule -R`.
  The host's `load_policy` and `semodule -B` pick the shipped `policy.35` and
  drop the operator's modules until its pod restarts.
- **`dontaudit` hides denials.** The base policy silences some; they still
  deny under enforcing.
- **No lines at all** means the audit filter is closed again: check
  `ssh <node> sudo auditctl -l` shows the two `msgtype` rules from
  `butane_node_config.yaml.j2` and nothing naming 1400 to 1499. The kernel's
  exclude filter drops whatever a rule matches, whatever its action.
- **Compare the categories.** When the rule is allowed and the denial stays,
  `scontext` and `tcontext` end in different pairs (`c12,c34`): the file was
  written by another pod. A reader of other pods' files, as a log collector
  is, takes `level: s0:c0.c1023`; two containers of one pod share a level
  once the type is set on the pod.
- `comm` is the thread name, not the pod: `log`, `ms_dispatch` and
  `admin_socket` are Ceph daemons. Map a `comm` to its pod with the MCS pair
  in `scontext` (`c123,c456`) and `ps -eZ` on the node.

## 2. Decide per workload

Work one workload at a time, most privileged first. Pick the first row that
fits; a policy module is the last resort.

| The denial shows | Change | Where |
| --- | --- | --- |
| A host agent by design: it reads other processes' `/proc`, loads BPF, mounts filesystems, reads the journal (Tetragon, Alloy, node-exporter, the Ceph daemons, CSI node plugins) | `securityContext.seLinuxOptions.type: spc_t` on that container. `spc_t` is unconfined, so only where the pod is already privileged in effect | The chart's values in the owning `application.yaml`. The namespace must be `privileged` in `pod-security.yaml`: `baseline` admits only `container_t`, `container_init_t`, `container_kvm_t` and `container_engine_t` |
| A container writing its own data under a hostPath (`var_lib_t`, `var_run_t`, `unlabeled_t` on a directory only this workload uses) | Label the directory `container_file_t` | A `chcon -R -t container_file_t <dir>` unit or tmpfiles entry in `butane_node_config.yaml.j2`, and by hand on running nodes. There is no `semanage` on Flatcar, so the label is not in the file-context database and a `restorecon` undoes it |
| A static control-plane pod (`etcd`, `kube-apiserver` on `/etc/kubernetes/pki`, the audit log) | Label the directories as above; `/var/lib/etcd` already is `container_file_t` | The same template. A kubeadm patch can add `seLinuxOptions` instead, see `/etc/kubernetes/patches` |
| Two pods sharing one volume or hostPath with different MCS pairs | The same `seLinuxOptions.level` on both, or `spc_t` for the reader | Chart values. Flatcar documents shared volumes as incompatible with enforcing otherwise |
| Something a boolean covers | `setsebool -P <name> on`; list them with `getsebool -a \| grep container` | The node template, as a oneshot unit |
| A workload that reads or writes a few host paths and nothing kernel-side (Alloy: the journal and log files) | A `SelinuxProfile`, step 3 | `payload/platform/security-profiles-operator/profiles.yaml` |

Never load `allow container_t ...` rules from the grouped output: they would
apply to every container on the node. That output names what is denied, it is
not a policy.

## 3. Write a `SelinuxProfile`

The operator's documented ways are recording a profile from a running pod or
writing one by hand. Recording needs `auditd` writing
`/var/log/audit/audit.log`, which the nodes do not have, so here a profile is
written by hand from the denials in Loki.

1. Draft the rules for one command:

    ```bash
    uv run python selinux_denials.py rules --since 7d --comm <comm>
    uv run python selinux_denials.py paths --since 24h --comm <comm>
    ```

2. Write the profile in `profiles.yaml`: `inherit` the `container` system
   profile, then under `allow` one map per target type, with classes and
   permissions. `@self` is the process's own type. Keep only what the
   workload needs (a denial is not proof of need; many are probes the program
   survives) and add `getattr` and `open` where a read was denied, since
   permissive mode logs each permission once.
3. The operator refuses a profile that allows the types `kernel_t`, `kmem_t`,
   `security_t` or `shadow_t`, the classes `capability`, `capability2`,
   `security` or `system`, or the permissions `mounton`, `relabelfrom`,
   `relabelto`, `transition`, `dyntransition`, `load_policy`, `mac_admin`,
   `setbool`, `setenforce` or `setsecparam`. A workload that needs one of
   those is a host agent: `spc_t`, step 2.
4. Merge the profile first and wait for it:

    ```bash
    kubectl get selinuxprofile <name>        # Installed, on every node
    ```

   Only then set `seLinuxOptions.type: <name>.process`, in a second merge. A
   pod whose type is not in the node's policy cannot start. Set it on the
   pod, not on one container: a container with its own `seLinuxOptions` gets
   its own category pair and can no longer read the pod's volumes, its
   ServiceAccount token included.
5. A custom type is admitted only where Pod Security is `privileged`
   (`pod-security.yaml`). `baseline` and `restricted` namespaces stay on
   `container_t`; a `ProfileBinding` can attach a profile by image without
   editing the pod, but the namespace rule holds for it too.

## 4. Where the policy lives on a node

`docs/platform/security-profiles-operator.md` has the detail. What matters
when working on denials:

- The operator's `selinuxd` installs modules into `/var/lib/selinux`, a
  writable copy `selinux-policy-store.service` makes at boot.
- `selinuxd` compiles `policy.33`; Flatcar ships `policy.35`. The host's own
  SELinux tools read and write only the latter. Do not run `semodule` or
  `load_policy` on a node by hand.
- A profile's type inherits the `container` template, generated by
  `gen_base_template.py` from a node's own policy so it carries everything
  `container_t` has; a profile only adds to that. After a Flatcar update that
  changes the container policy, regenerate it and give the ConfigMap a new
  name.
- To try a template or raw CIL without touching a node, run the `el9`
  `selinuxd` image's `semodule -n -i` against a copy of the two directories.
- Whatever a node needs to boot (the static control-plane pods, the container
  runtime's own transitions) cannot wait for the operator's pod and belongs in
  `butane_node_config.yaml.j2`.

## 5. Mechanisms

| Mechanism | What it manages |
| --- | --- |
| `securityContext.seLinuxOptions.type: spc_t` | A host agent runs unconfined, like a privileged container |
| `securityContext.seLinuxOptions.level` | Two pods share one MCS pair, and so a volume |
| A label on a host path (`chcon -t container_file_t`) | A confined pod may use that directory |
| `SelinuxProfile` in `profiles.yaml` | A type of its own for one workload |
| `seLinuxChangePolicy` and the `SELinuxMount` gates | How a volume is relabelled for a pod; only matters once enforcing |

## 6. Trial before enforcing

1. Merge the change, restart the workload on one worker, and confirm its
   `comm` is gone from `rules --since 15m --node <node>`. A new type starts
   with only what `container` grants, so expect a second round of denials.
2. Then `sudo setenforce 1` on that worker. It is runtime only and a reboot
   returns it to permissive. Restart the pods that touch the host and watch
   `kubectl get pods -A --field-selector spec.nodeName=<node>`; under
   enforcing a blocked pod crash-loops with `permission denied` on a path.
3. Control planes last, and only after `etcd` and `kube-apiserver` show no
   denials: enforcing stops them at their next restart otherwise.
4. Persisting enforcing is a node-template change
   (`/etc/selinux/config`, a symlink Flatcar's page says to replace first)
   rolled out with the `node-rollout` skill.

## 7. Report

Per workload: the denials grouped, the row of step 2 chosen and why, the
file changed, and the check that its denials stopped. Then what is still
logging and which node was trialled.
