---
description: "Bring up the bare metal Kubernetes cluster from scratch: provision nodes over PXE, bootstrap with Kubeadm, and install the core platform."
---

# Quickstart

![Homelab Logo](assets/images/logo.png){ align=right width=150 }

Bring up the bare metal Kubernetes cluster from scratch. This project automates
the deployment using Flatcar Container Linux and Kubeadm.

Set aside an afternoon. Not because the steps are long — they are eleven
commands — but because somewhere around step 7 a machine will sit at a blinking
cursor, and you will learn something about your DHCP server that you did not
want to know.

!!! warning "Read this first if the cluster isn't mine"
    This repository describes a specific homelab. Node names, IP addresses, the `wlkr.ch` domain, and the Git repository URL are hardcoded throughout `payload/`. Following the steps below verbatim gives you a cluster whose ArgoCD syncs from **this** repository and whose certificates are issued for a domain you don't control. Work through [Adapting This for Your Cluster](adapting.md) **before** step 1.

## Hardware Requirements

Each node must have:

- A NIC that supports PXE booting
- An NVMe drive (or adjust `install_disk` in `inventory.yaml` — one node uses `/dev/sda`, because hardware is a collection of exceptions wearing a trenchcoat)
- Sufficient disk space: Flatcar itself plus a 25 GB root, 50 GB for containerd, 40 GB for the kubelet, 10 GB for `/var/log`, and the remainder used by Rook-Ceph as OSD storage. 256 GB leaves about 111 GB for Ceph per node
- At least 8 GB of RAM: the installer stages the ~700 MB Flatcar image in the PXE environment's tmpfs before writing it

The deployment host (the machine running Ansible and the boot server) must be reachable from the nodes on the same L2 network segment.

## Prerequisites

### Tools on the deployment host

| Tool | Used by | Install |
| --- | --- | --- |
| `make` | every step | Xcode CLT / `build-essential` |
| `git` | cloning this repository | your package manager |
| `uv` | Ansible and the boot server | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `butane` | transpiling Butane YAML to Ignition JSON | `brew install butane` or the [Flatcar docs](https://www.flatcar.org/docs/latest/provisioning/config-transpiler/) |
| `kubectl` | steps 8 onwards | [kubernetes.io](https://kubernetes.io/docs/tasks/tools/) |
| `helm` | `make install-core`, `make install-argo` | [helm.sh](https://helm.sh/docs/intro/install/) |
| `sudo` | `make serve` binds privileged port 69 | — |

### Other requirements

- **SSH Key**: An Ed25519 key at `~/.ssh/id_ed25519.pub` (or edit `ansible/templates/butane_config.yaml.j2` to use a different path/key)
- **External DHCP Server**: Must point PXE clients at the deployment host:
  - Option 66 (`next-server`): IP of the machine running `make serve`
  - Option 67 (`filename`): `lpxelinux.0` for BIOS, `syslinux.efi` for UEFI

!!! tip "Consumer routers and PXE"
    Plenty of home routers will happily let you set options 66 and 67 and then serve neither. If step 7 produces total silence in the boot server log, prove the DHCP side first with `tcpdump -i <iface> port 67 or port 68` before you go looking for bugs in anything more interesting.

## Setup

1. **Clone Repository**:

    ```bash
    git clone https://github.com/JanWelker/homelab.git homelab
    cd homelab
    ```

2. **Configure Inventory**:
    Edit `ansible/inventory.yaml` to define your target nodes and settings.

    | Variable | Why it matters |
    | --- | --- |
    | `boot_server_ip` | **Most commonly missed.** Baked into the generated PXE menu as the URL for the kernel, initrd, and Ignition config. If this isn't the IP of the machine that will run `make serve`, nodes load the bootloader and then hang. |
    | `mac_address` (per host) | Selects which generated PXE menu a node picks up |
    | `ansible_host` (per host) | The static IP the node is given |
    | `install_disk` | Target disk for the Flatcar install (`/dev/nvme0n1` by default). **Wiped completely** — partitions, GPT and a device-level discard — before the install |
    | `kubernetes_version`, `containerd_version`, `flatcar_version` | Artifact versions to download |

3. **Initialize Environment**:
    Initialize the project using `uv` to create the virtual environment and install dependencies:

    ```bash
    make setup
    ```

4. **Download Artifacts**:

    ```bash
    make download
    ```

    *Downloads Flatcar artifacts, Syslinux, and Systemd Sysext images
    (Kubernetes, Containerd) to `output/http`.*

5. **Generate Configurations**:

    ```bash
    make config
    ```

    *Artifacts will be generated in `output/http` (Ignition) and `output/tftp`
    (PXE). Two Ignition configs are written per host: `ignition-<host>.json`,
    which is embedded into the installed system, and `ignition-install-<host>.json`,
    the throwaway installer that wipes the disk and runs `flatcar-install`. The
    install disk ends up as a 25GB root, 50GB for containerd, 40GB for the
    kubelet root directory, 10GB for `/var/log`, and the remaining space for
    Rook-Ceph storage.*

    Re-run this after **any** change to `inventory.yaml` — the values are baked
    into the generated files. Editing the inventory and skipping this step is the
    homelab equivalent of changing the config and forgetting to reload the
    service, and it fails just as quietly. `make artifacts` runs steps 4 and 5
    together.

    Check that one PXE menu was generated per node:

    ```bash
    ls output/tftp/pxelinux.cfg/     # one 01-<mac> file per host
    ```

6. **Start Boot Server** (Requires sudo for port 69):

    ```bash
    make serve
    ```

    Leave this running for the whole of step 7 — it serves every artifact the
    nodes fetch. It logs each TFTP and HTTP request, which is the best signal
    that a node is progressing. Keep the window visible: watching those requests
    arrive in order is the single most useful debugging tool in this entire
    procedure.

7. **Arm the install, then boot the machines**:

    ```bash
    make reinstall
    ```

    The generated PXE menus default to `Boot from local disk`, which is what you
    want on every boot *except* this one — it is why a reboot later cannot
    reinstall a node. `make reinstall` flips that to `Install` in
    `output/tftp/pxelinux.cfg/`, for every host or for `LIMIT=<node>`;
    `make reinstall-cancel` puts it back, as does re-running `make config`.

    Then power on your bare metal nodes. (Selecting `Install` from the menu at
    each console does the same thing, if you would rather not arm anything.)
    See [Boot & Bootstrap Process](architecture/boot-process.md).

    - The node wipes the disk, writes Flatcar, and reboots into it. From that
      reboot on it is booting from its own disk.
    - Expect the boot server log to show, per node: a TFTP request for the
      bootloader and its `01-<mac>` menu, then HTTP requests for the kernel,
      the initrd, `ignition-install-<host>.json`, the Flatcar disk image, and
      `ignition-<host>.json` — then, after the reboot, only the sysext images.
    - **Leave the boot server running until every node is up.** The sysexts are
      fetched on the first boot from disk. After that nothing needs it.
    - **Note**: The cluster will come up in a `NotReady` state initially because
      no CNI is installed. This is correct. Do not fix it yet.
    - If a node stalls, see [Troubleshooting PXE boot](#troubleshooting-pxe-boot).

    !!! tip "Watching an install"
        `flatcar-install.service` logs to the console, so a monitor on the node
        shows the wipe and the write. Over SSH, `journalctl -u flatcar-install -f`
        as `core@<node>` does the same. A failed install deliberately does *not*
        reboot — the node stays in the PXE environment with the journal intact.

8. **Retrieve Kubeconfig**:
    Once the control plane node responds to SSH (or is pingable), retrieve the
    admin kubeconfig:

    ```bash
    make kubeconfig
    ```

    Verify the cluster answers. `NotReady` is expected at this point:

    ```bash
    export KUBECONFIG="$PWD/output/kubeconfig"
    kubectl get nodes
    ```

    *Optional: Install to local machine (will not overwrite existing config):*

    ```bash
    mkdir -p ~/.kube
    cp -n output/kubeconfig ~/.kube/config
    ```

9. **Install Core Components** (CRITICAL):
    With `output/kubeconfig` in place (`kubeadm` likely finished):

    ```bash
    make install-core
    ```

    - Installs the Gateway API and Prometheus operator CRDs, then **Cilium**
      (CNI, Ingress, L2 Announcements) via Helm.
    - **Removes** `kube-proxy` to resolve IPVS conflicts.
    - Installs **cert-manager** (for ACME TLS) and the Let's Encrypt
      ClusterIssuers.

    !!! warning "Single-node clusters: untaint first"
        cert-manager ships no tolerations, so on one tainted node this target hangs at `rollout status deploy/cert-manager` and never returns. Run `make untaint` before it — see [Single-node clusters](#single-node-clusters).

    !!! note
        This target runs before ArgoCD exists, so it installs Cilium, cert-manager and the Gateway API CRDs directly. It carries no version pins of its own: the `Makefile` reads each version out of the ArgoCD `Application` that adopts the component later, so what bootstrap installs is what ArgoCD then reconciles, and Renovate only ever has one number to move. The Prometheus operator CRDs ride along for the same reason in reverse: Cilium's chart refuses to render at all while `monitoring.coreos.com/v1` is missing, and kube-prometheus-stack, which owns those CRDs, arrives several sync waves later. See [Monitoring](platform/monitoring.md#crds).

    Nodes should reach `Ready` once Cilium is up:

    ```bash
    kubectl -n kube-system rollout status ds/cilium
    kubectl get nodes                  # all Ready
    ```

10. **Post-Installation**:

    - **Deploy ArgoCD**:

        ```bash
        make install-argo
        ```

        There is no way into the UI yet, and that is expected. The local
        admin account is disabled in `payload/argocd/values.yaml`, so the
        server never generates `argocd-initial-admin-secret`; Authentik, the
        only other way in, arrives later through GitOps and needs the client
        secret that OpenBao does not hold until step 11. Follow the bootstrap
        with `kubectl` instead.

        If you want the UI before then, re-enable the local admin for as long
        as you need it. The password is generated the first time the server
        starts with the account enabled, so the restart is what creates the
        Secret:

        ```bash
        kubectl -n argocd patch cm argocd-cm --type merge \
          -p '{"data":{"admin.enabled":"true"}}'
        kubectl -n argocd rollout restart deploy/argocd-server
        kubectl -n argocd rollout status deploy/argocd-server

        kubectl -n argocd get secret argocd-initial-admin-secret \
          -o jsonpath='{.data.password}' | base64 -d; echo
        ```

        The Gateway and DNS for `argo.infra.k8s.wlkr.ch` don't work yet, so
        reach it by port-forward and log in as `admin`:

        ```bash
        kubectl -n argocd port-forward svc/argocd-server 8080:80
        # http://localhost:8080
        ```

        Set `admin.enabled` back to `"false"` once Authentik can log you in.
        It is the same escape hatch you will reach for on the day SSO is down,
        documented at
        [Authentik &rarr; When Authentik is down](platform/authentik.md#when-authentik-is-down).

    - **Bootstrap GitOps** (App-of-Apps):

        ```bash
        make bootstrap-apps
        ```

        *This applies the `apps`, `infra` and `system` AppProjects and then
        the parent applications (platform, gitops) which enable ArgoCD to
        manage all applications from Git.* The projects go on first because
        both parent applications name one, and the file defining them is
        synced by `gitops` — which needs `system` to exist before it can sync
        anything. ArgoCD adopts that file on the first sync, so this is the
        same bootstrap-by-hand handover as `make install-core`.

        This is the handover moment: from here on the cluster takes its orders
        from the repository rather than from you. Watch the platform sync.
        Applications appear in [sync-wave order](platform/index.md#usage):

        ```bash
        kubectl -n argocd get applications -w
        ```

        The `Certificate` resources stay un-Ready until step 11 — that is
        expected, since their Route53 credentials don't exist yet.

    - **Gate on storage** before trusting the workloads that need it:

        ```bash
        make storage-check
        ```

        The sync waves put Rook ahead of everything that mounts a volume, which
        is not the same as Rook being able to serve one: a `CephCluster` reports
        `Ready` with no OSDs, and a `StorageClass` exists whether or not a CSI
        driver registered for it. Either way the later waves start regardless
        and their pods sit in `Pending`. This asks for a volume the way a
        workload would and names the first broken link if it does not get one —
        see [Rook-Ceph &rarr; Is storage
        ready?](platform/rook-ceph.md#is-storage-ready).

11. **Initialise the secret store**:
    OpenBao starts uninitialised, sealed and empty, and four of the platform
    components read their credentials out of it through
    [ExternalSecrets](platform/external-secrets.md). Until it is initialised,
    unsealed and populated, none of those Secrets exist, so cert-manager issues
    no certificates and the pods that mount one never start:

    ```text
    Warning  Failed  12s (x7 over 72s)  kubelet  Error: secret "route53-credentials" not found
    Warning  Failed   2s (x7 over 69s)  kubelet  Error: secret "authentik-secrets" not found
    ```

    That is expected until this step is done; the pods recover on their own once
    the Secrets materialise.

    ```bash
    make bao-init
    ```

    That initialises OpenBao with 5 key shares and a threshold of 3, unseals all
    three replicas, enables the `kv` v2 engine, enables the Kubernetes auth
    method, and writes the policy and role External Secrets authenticates with.
    It writes the keys and root token to `output/credentials/openbao-init.json`.

    !!! danger "Move those keys before you do anything else"
        That file is a plaintext copy of the keys to every secret the cluster
        holds. Copy them into a password manager and delete it. Losing all five
        means the data is unrecoverable — there is no support line and no
        recovery flow. See [OpenBao &rarr; Bootstrap](platform/openbao.md#bootstrap).

    Then populate the four paths the cluster reads. Three values belong to
    accounts outside the cluster and have to be supplied; everything under
    `kv/authentik/config`, including the OIDC client credentials ArgoCD and
    Grafana read back, is generated:

    ```bash
    CERT_MANAGER_KEY_ID=AKIA... CERT_MANAGER_SECRET_KEY=... \
    EXTERNAL_DNS_KEY_ID=AKIA... EXTERNAL_DNS_SECRET_KEY=... \
    SMTP_PASSWORD=... \
    make bao-secrets
    ```

    Two Route53 IAM users on purpose: cert-manager only ever writes
    `_acme-challenge` TXT records, external-dns creates and deletes an A record
    for every hostname. Existing paths are left alone — rewriting
    `kv/authentik/config` on a running cluster rotates Authentik's Postgres
    password out from under its database.

    Cert-manager then picks up the materialised Secret and issues the gateway
    certificates, and the pods that were waiting on the others start on the next
    ESO refresh. Authentik's is what finally gives you a login for the ArgoCD
    and Grafana UIs.

12. **Create the first administrator**:
    There is no sign-up, and no user to be created: Authentik ships the built-in
    `akadmin` account, and its password is the `bootstrap-password` step 11
    generated and never showed you. Read it back out of OpenBao:

    ```bash
    kubectl -n openbao exec openbao-0 -- \
      bao kv get -mount=kv -field=bootstrap-password authentik/config
    ```

    (That needs a token — `bao login` inside the pod, or the port-forward in
    [OpenBao &rarr; Authenticate locally](platform/openbao.md#3-authenticate-locally).
    `make bao-secrets` prints the same command when it finishes.)

    Log in at
    [auth.infra.k8s.wlkr.ch](https://auth.infra.k8s.wlkr.ch) as `akadmin`, then
    create the four groups under *Directory &rarr; Groups* and add yourself to
    the ones you need — see
    [Authentik &rarr; Groups and roles](platform/authentik.md#groups-and-roles):

    | Group | Grants |
    | --- | --- |
    | `argocd-admins` | ArgoCD `role:admin` |
    | `argocd-viewers` | ArgoCD `role:readonly` |
    | `grafana-admins` | Grafana `Admin` |
    | `grafana-editors` | Grafana `Editor` |

    Do the groups before you try the other UIs, because membership is the whole
    of authorisation here. ArgoCD's `policy.default` is empty, so a user in
    neither ArgoCD group is authenticated and entitled to nothing — an SSO login
    that succeeds and lands on an ArgoCD with no applications in it is this, not
    a broken integration.

    Two follow-ups worth doing the same evening: create a personal account in
    *Directory &rarr; Users*, put it in the groups, and use that from then on,
    leaving `akadmin` as the break-glass identity for the day SSO is the thing
    that is broken — see
    [When Authentik is down](platform/authentik.md#when-authentik-is-down). And
    keep `bootstrap-password` in OpenBao rather than rotating it out: it is what
    the account falls back to after a rebuild.

## Single-node clusters

The [documented layout](architecture/index.md#cluster-layout) has dedicated
worker nodes, so the control-plane taint stays in place. If you are instead
running everything on one node, remove the taint before step 9 so workloads can
schedule:

```bash
make untaint
```

Before, not after, because step 9 does not survive the taint. Cilium does: the
agent and Envoy DaemonSets tolerate every taint and the operator tolerates the
control-plane one by name, so the node reaches `Ready` and it looks like the
taint is not in the way. cert-manager carries no tolerations at all, so
`make install-core` gets as far as `rollout status deploy/cert-manager` and
stops there: none of its three Deployments can be placed, and `rollout status`
has no timeout, so it waits for a scheduling decision that is never coming.
Hubble's relay and UI are Pending for the same reason, quietly, because nothing
waits on them.

The taint can come off any time after step 8 puts a kubeconfig in place. The
node being `NotReady` until Cilium lands does not matter — this is an API call
against the node object, not something that has to schedule.

Re-apply it when you later add worker nodes:

```bash
make taint
```

## Reprovisioned nodes

A rebuild onto the same hardware used to inherit the previous cluster's OSDs.
Butane created the `rook-osd` partition only when it was missing and left it
unformatted, so the old BlueStore signature survived — and Rook will not adopt
an OSD belonging to a cluster it does not know.

The installer now wipes the disk before `flatcar-install` runs: filesystem
signatures off every partition, the GPT zapped, and a device-level discard where
the hardware supports it. So picking `install` from the PXE menu does hand Ceph
an empty disk back.

Worth keeping the symptom in mind anyway, because it is what you would see if
that wipe ever failed — or on hardware where `blkdiscard` is declined *and*
`wipefs` missed something. There are no `rook-ceph-osd` pods, the `CephCluster`
still reports `Ready`, and the failure surfaces one layer up, at step 11:

```console
$ kubectl -n openbao exec -it openbao-0 -- bao operator init
error: unable to upgrade connection: pod openbao-0 does not have a host assigned
```

`openbao-0` is `Pending` on an unbound PVC because the cluster has no OSDs to
provision one from. `make storage-check` says so in one line. The fix is to
rebuild the node — `make reinstall LIMIT=<node>` and network-boot it, which
wipes the disk on the way in: [Rook-Ceph &rarr; No OSDs after
reprovisioning](platform/rook-ceph.md#no-osds-after-reprovisioning).

## Verifying the result

The five commands that answer "is it actually fine?":

```bash
kubectl get nodes                                  # all Ready
kubectl -n argocd get applications                 # all Synced / Healthy
kubectl get certificate -A                         # READY=True
kubectl -n rook-ceph get cephcluster               # HEALTH_OK
make storage-check                                 # a PVC actually binds
```

The last one earns its place: the `cephcluster` line above is the one that
lies, reporting `Ready` on a cluster with no OSDs and no CSI driver.

Once DNS points at the gateway IPs, the platform UIs are reachable — see
[Platform &rarr; HTTPRoute Locations](platform/index.md#httproute-locations).

## Troubleshooting PXE boot

Step 7 is the most failure-prone part of the process, and it fails in a
particularly demoralising way: a black screen with a blinking cursor and no
error message. The trick is to stop staring at the node and start reading the
`make serve` log, asking one question — how far did it get?

| Symptom | Likely cause |
| --- | --- |
| Node never requests anything; no log output at all | DHCP isn't handing out options 66/67, or the node isn't on the same L2 segment. Check the DHCP lease and that PXE is enabled in firmware. |
| `PXE-E32: TFTP open timeout` | `make serve` isn't running, or a firewall is blocking UDP/69. On macOS, allow the Python interpreter through the firewall. |
| Bootloader loads, then "Could not find kernel image" or a hang at the menu | `boot_server_ip` in `inventory.yaml` is wrong. It is baked into the menu's kernel/initrd URLs. Fix it, re-run `make config`, and reboot the node. |
| TFTP requests arrive but no `01-<mac>` file is served | The node's `mac_address` in `inventory.yaml` doesn't match its actual NIC. Compare against `ls output/tftp/pxelinux.cfg/`. |
| Kernel boots, then Ignition fails | The node couldn't fetch `ignition-<host>.json` over HTTP (port 8000), or the Butane template references an SSH key path that doesn't exist. |
| Node installs but never joins the cluster | Sysext download failed, or the kubeadm systemd unit errored. SSH in as `core` and check `journalctl -u kubeadm`. |

Two things worth internalising. A machine with two NICs will PXE boot from
whichever one it feels like, and the MAC printed on the case is frequently not
that one. And a node that boots the menu but stalls immediately after is almost
never a broken image — it is `boot_server_ip`, pointing at an address that was
correct on some other network, on some other day.

The boot server serves `output/tftp` over TFTP and `output/http` over HTTP —
see [Boot Server](boot_server/index.md). If a file is missing from those
directories, re-run `make artifacts`.
