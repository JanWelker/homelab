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
  the pod restarts. To make running processes log again, flush the cache on
  that node: `ssh <node> sudo load_policy`. Then restart the pod.
- **`dontaudit` hides denials.** The base policy silences some; they still
  deny under enforcing. `semodule -DB` rebuilds without them, which on Flatcar
  needs the writable policy store from step 4.
- **No lines at all** means the audit filter is closed again: check
  `ssh <node> sudo auditctl -l` shows the two `msgtype` rules from
  `butane_node_config.yaml.j2` and nothing naming 1400 to 1499. The kernel's
  exclude filter drops whatever a rule matches, whatever its action.
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
| None of the above | A policy module for a type of its own, step 3 | |

Never load `allow container_t ...` rules from the grouped output: they would
apply to every container on the node. That output names what is denied, it is
not a policy.

## 3. Write a module

For a workload that should stay confined but needs more than `container_t`.

1. Draft it from the denials:

    ```bash
    uv run python selinux_denials.py te --since 7d --comm <comm> --module <name>_local > <name>_local.te
    ```

2. Edit it: give the workload its own type instead of `container_t`, drop
   every rule it does not need (a denial is not proof of need; many are
   probes the program survives), and replace broad targets such as
   `unlabeled_t` with a label change from step 2.
3. Build on a node, which has `checkmodule` and `semodule_package` but no
   `audit2allow` or `secilc`:

    ```bash
    checkmodule -M -m -o <name>_local.mod <name>_local.te
    semodule_package -o <name>_local.pp -m <name>_local.mod
    ```

4. Deliver it: as a `SelinuxProfile` through the operator, step 5, or by
   hand, step 4. Then point the pod at the type with `seLinuxOptions.type`.

## 4. Load a module on Flatcar

Flatcar ships the policy read-only under `/usr/lib/selinux`; `semodule -l`
lists nothing until the store is writable. Its documentation
(<https://www.flatcar.org/docs/latest/security/encryption/selinux/>) copies
`/usr/lib/selinux/mcs` and `/usr/lib/selinux/policy` into `/etc/selinux` and
`/var/lib/selinux` first; follow that page, on one worker, before
`semodule -i <name>_local.pp`. This has not been done on this cluster: treat
the first time as a trial, and put the result into the node template, since a
reinstall starts from the shipped policy again.

## 5. Manage it through Kubernetes

| Mechanism | What it manages | State here |
| --- | --- | --- |
| `securityContext.seLinuxOptions` (`type`, `level`) | Which existing type and MCS pair a pod runs as | Native, declarative in chart values; covers every row of step 2 except the labels |
| `seLinuxChangePolicy` and the `SELinuxMount` gates | How a volume is relabelled for the pod, by mount option or recursively | Native; only matters once enforcing |
| [Security Profiles Operator](https://github.com/kubernetes-sigs/security-profiles-operator), `SelinuxProfile` | Policy modules as cluster objects: the `spod` DaemonSet's `selinuxd` installs them on every node, a pod uses `<profile>.process` as its type, and it can record a profile from the audit log | The chosen way to manage modules (#1696), not installed yet |

Use the first row wherever an existing type fits; a module, and so the
operator, is for what is left. Before the operator becomes an Application,
answer these on one worker:

- `selinuxd` runs `semodule` against the host's policy store, so it needs
  the writable store of step 4. Whether it works on Flatcar is untested.
- SELinux support is off outside OpenShift: `spec.selinux.enable: true` on
  the `spod` object.
- Flatcar's base policy does not match the operator's bundled templates; its
  configuration page names Flatcar and `selinux.customTemplatesConfigMap`,
  a ConfigMap of `.cil` files, as the answer.
- A custom type is admitted only in a namespace whose Pod Security level is
  `privileged`.
- A node reinstalled from Ignition has no modules until the `spod` pod runs
  there, so anything a node needs to boot (the static control-plane pods)
  belongs in the node template, not in a `SelinuxProfile`.

A `SelinuxProfile` is the module of step 3 in YAML: `inherit` the `container`
system profile, then one map per target type and class under `allow`, with
`@self` for the process's own type.

## 6. Trial before enforcing

1. Merge the change, let it reach one worker, flush the cache there
   (`sudo load_policy`), restart the workload, and confirm its `comm` is gone
   from `rules --since 15m --node <node>`.
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
