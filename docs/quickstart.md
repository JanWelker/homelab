---
description: "Bring up the bare metal Kubernetes cluster from scratch: provision nodes over PXE, bootstrap with Kubeadm, and install the core platform."
---

# Quickstart

![Homelab Logo](assets/images/logo.png){ align=right width=150 }

Bring up the bare metal Kubernetes cluster from scratch, using Flatcar
Container Linux and Kubeadm. The steps are thirteen commands; set aside an
afternoon anyway, because step 7 depends on your DHCP server behaving.

!!! warning "Read this first if the cluster isn't mine"
    Node names, IP addresses, the `wlkr.ch` domain, and the Git repository URL are hardcoded throughout `payload/`. Following the steps below verbatim gives you a cluster whose ArgoCD syncs from **this** repository and whose certificates are issued for a domain you don't control. Work through [Adapting This for Your Cluster](adapting.md) **before** step 1.

## Hardware Requirements

Each node must have:

- A NIC that supports PXE booting
- An NVMe drive, or a different `install_disk` in `inventory.yaml`
- Enough disk for Flatcar, a 50 GB root filesystem, and the remainder, which
  Rook-Ceph takes as OSD storage
- At least 4 GB of RAM: the installer runs from a RAM disk and streams the
  Flatcar image straight to disk

The deployment host (the machine running Ansible and the boot server) must be on
the same L2 network segment as the nodes.

## Prerequisites

### Tools on the deployment host

| Tool | Used by | Install |
| --- | --- | --- |
| `make` | every step | Xcode CLT / `build-essential` |
| `git` | cloning this repository | your package manager |
| `uv` | Ansible and the boot server | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `butane` | transpiling Butane YAML to Ignition JSON | `brew install butane` or the [Flatcar docs](https://www.flatcar.org/docs/latest/provisioning/config-transpiler/) |
| `kubectl` | steps 8 onwards | [kubernetes.io](https://kubernetes.io/docs/tasks/tools/) |
| `helm` | `make install-cilium`, `make install-argo` | [helm.sh](https://helm.sh/docs/intro/install/) |
| `sudo` | `make serve` binds privileged port 69 | — |

### Other requirements

- **SSH Key**: An Ed25519 key at `~/.ssh/id_ed25519.pub` (or edit `ansible/templates/butane_node_config.yaml.j2` and `butane_installer_config.yaml.j2` to use a different path/key)
- **External DHCP Server**: Must point PXE clients at the deployment host:
  - Option 66 (`next-server`): IP of the machine running `make serve`
  - Option 67 (`filename`): `lpxelinux.0` for BIOS, `syslinux.efi` for UEFI

!!! tip "Consumer routers and PXE"
    Many home routers accept options 66 and 67 and then serve neither. If step 7 produces silence in the boot server log, prove the DHCP side first with `tcpdump -i <iface> port 67 or port 68`.

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
    Creates the `uv` virtual environment and installs dependencies:

    ```bash
    make setup
    ```

4. **Download Artifacts**:
    Fetches Flatcar, Syslinux and the Kubernetes and containerd sysext images
    into `output/http`:

    ```bash
    make download
    ```

5. **Generate Configurations**:

    ```bash
    make config
    ```

    Writes Ignition configs to `output/http` and PXE menus to `output/tftp`.
    Each host gets `ignition-<host>-install.json`, which the PXE environment
    runs to wipe the disk and install, and `ignition-<host>.json`, which
    `flatcar-install` embeds into the installed system — see
    [Boot & Bootstrap Process](architecture/boot-process.md).

    Re-run this after **any** change to `inventory.yaml`; the values are baked
    into the generated files. `make artifacts` runs steps 4 and 5 together.
    Check that one PXE menu was generated per node:

    ```bash
    ls output/tftp/pxelinux.cfg/     # one 01-<mac> file per host
    ```

6. **Start Boot Server** (requires sudo for port 69):

    ```bash
    make serve
    ```

    Leave this running through step 7 and keep the window visible: it names
    each node and logs one line per request. Its first two lines say what each
    node will do when powered on; a node under *booting from disk* will not
    install, however often you reboot it.

    ```console
    20:33:04  server        armed to install: odin, thor
    20:33:04  server        booting from disk: freya, heimdall, loki, valkyrie
    20:34:17  odin          collecting its boot menu -- armed, so it will install
    20:34:48  odin          collecting the OS image (1.2 GB) -- this is the long one
    20:36:12  odin          OS image delivered -- switching to local boot, so the reboot lands on the disk
    ```

7. **Arm the install, then boot the machines**:

    ```bash
    make reinstall
    ```

    The generated PXE configs default to booting the local disk; `make reinstall`
    flips them to `Install`, for `LIMIT=<node>` or, after a typed confirmation,
    for every host. The boot server disarms a node itself once it has delivered
    the OS image, so the reboot at the end of the install lands on the disk —
    see [Switching back to local boot](architecture/boot-process.md#switching-back-to-local-boot).

    Then power on the nodes. No menu appears; each node does what it was armed
    to do, and the boot server log walks it through the fetches, then
    `switching to local boot`, then after the reboot the Ignition config again
    plus the sysext images. A node still shown by bare IP has not fetched a
    menu the server recognises.

    - **Leave the boot server running until every node is up.** The sysexts
      are fetched on the first boot from disk.
    - The cluster comes up `NotReady` because no CNI is installed. This is
      correct; do not fix it yet.
    - If a node stalls, see [Troubleshooting PXE boot](#troubleshooting-pxe-boot).

    !!! tip "Watching an install"
        `journalctl -u flatcar-install -f` as `core@<node>` shows the wipe and the write (so does a monitor on the node). A failed install does *not* reboot — the node stays in the PXE environment with the journal intact.

8. **Retrieve Kubeconfig**:
    Once the control plane node responds to SSH:

    ```bash
    make kubeconfig
    ```

    Verify the cluster answers. `NotReady` is expected at this point:

    ```bash
    export KUBECONFIG="$PWD/output/kubeconfig"
    kubectl get nodes
    ```

    *Optional: install to the local machine (will not overwrite an existing config):*

    ```bash
    mkdir -p ~/.kube
    cp -n output/kubeconfig ~/.kube/config
    ```

9. **Install Cilium** (CRITICAL):

    ```bash
    make install-cilium
    ```

    Installs the Gateway API CRDs, then Cilium via Helm — only what ArgoCD
    needs in order to run; everything else arrives through ArgoCD, at the
    versions pinned in each component's `application.yaml` (see
    [GitOps Strategy &rarr; Version pins](architecture/gitops.md#version-pins)
    and [Cilium &rarr; Installation](platform/cilium.md#installation)).
    `make bootstrap` runs this step, `make install-argo` and
    `make bootstrap-apps` in one go.

    Nodes reach `Ready` once Cilium is up:

    ```bash
    kubectl -n kube-system rollout status ds/cilium
    kubectl get nodes                  # all Ready
    ```

10. **Post-Installation**:

    - **Deploy ArgoCD**:

        ```bash
        make install-argo
        ```

        There is no way into the UI yet: the local admin is disabled in
        `payload/argocd/values.yaml` and Authentik arrives later through
        GitOps. Follow the bootstrap with `kubectl` instead.

        **Tip — the UI before Authentik exists:** re-enable the local admin
        for as long as you need it. The restart is what generates the
        password Secret:

        ```bash
        kubectl -n argocd patch cm argocd-cm --type merge \
          -p '{"data":{"admin.enabled":"true"}}'
        kubectl -n argocd rollout restart deploy/argocd-server
        kubectl -n argocd rollout status deploy/argocd-server

        kubectl -n argocd get secret argocd-initial-admin-secret \
          -o jsonpath='{.data.password}' | base64 -d; echo
        kubectl -n argocd port-forward svc/argocd-server 8080:80   # http://localhost:8080, user admin
        ```

        Set `admin.enabled` back to `"false"` once Authentik can log you in —
        see [Authentik &rarr; When Authentik is down](platform/authentik.md#when-authentik-is-down).

    - **Hand the cluster over to ArgoCD**:

        ```bash
        make bootstrap-apps
        ```

        Applies the `apps`, `infra` and `system` AppProjects, then the
        self-managing `argocd` Application, which brings the `platform`
        ApplicationSet and with it every component, one
        [stage](platform/index.md#rollout-order) at a time. Watch which stage
        it is on:

        ```bash
        kubectl -n argocd get applicationset platform -o jsonpath=\
        '{range .status.applicationStatus[*]}{.step}{"\t"}{.status}{"\t"}{.application}{"\n"}{end}'
        ```

        **The rollout stops at `05-secrets`, and that is expected.** `openbao`
        cannot go Healthy until step 11 — see
        [Bootstrap pauses at OpenBao](architecture/gitops.md#bootstrap-pauses-at-openbao).

    - **Gate on storage** before trusting anything that mounts a volume:

        ```bash
        make storage-check
        ```

        A `StorageClass` exists whether or not a CSI driver registered for it,
        so `04-storage` can finish with no working provisioner. This asks for
        a volume the way a workload would and names the first broken link —
        see [Rook-Ceph &rarr; Is storage ready?](platform/rook-ceph.md#is-storage-ready).

11. **Initialise the secret store**:
    OpenBao starts uninitialised, sealed and empty; the rollout holds at
    `05-secrets` until it is unsealed and at `06-certificates` until it is
    populated, then resumes on its own.

    ```bash
    make bao-init
    ```

    Initialises and unseals OpenBao, enables the `kv` v2 engine and the
    Kubernetes auth method, and writes the policy and role External Secrets
    authenticates with. Keys and root token land in
    `output/credentials/openbao-init.json`.

    !!! danger "Move those keys before you do anything else"
        That file is a plaintext copy of the keys to every secret the cluster holds. Copy them into a password manager and delete it. Losing them means the data is unrecoverable. See [OpenBao &rarr; Bootstrap](platform/openbao.md#bootstrap).

    Then populate the paths the cluster reads:

    ```bash
    make bao-secrets
    ```

    It prompts, with input hidden, for the values that belong to accounts
    outside the cluster: two Route53 IAM key pairs (why two is in
    [OpenBao](platform/openbao.md)), and the SMTP login, password and alert
    recipient. Everything under `kv/authentik/config`, including the OIDC
    client credentials ArgoCD and Grafana read back, is generated, as is
    Grafana's break-glass admin password. Existing paths are left alone;
    rewriting `kv/authentik/config` on a running cluster rotates Authentik's
    Postgres password out from under its database.

    !!! tip "Paste them at the prompt, not onto a command line"
        A secret containing `#` on a command line is truncated at it, and one containing `!` is mangled by history expansion — both silently. Non-interactively, the matching environment variables are honoured when already set; quote them with **single** quotes.

    Once the store validates, `certificates` issues the gateway certificates
    and the later stages sync. The store is re-checked every few minutes, so
    the next stage can take that long to start.

12. **Create the first administrator**:
    Authentik ships the built-in `akadmin` account; its password is the
    `bootstrap-password` step 11 generated. Read it back out of OpenBao (needs
    a token — `bao login` inside the pod, or the port-forward in
    [OpenBao &rarr; Authenticating locally](platform/openbao.md#authenticating-locally);
    `make bao-secrets` prints the same command when it finishes):

    ```bash
    kubectl -n openbao exec openbao-0 -- \
      bao kv get -mount=kv -field=bootstrap-password authentik/config
    ```

    Log in at [auth.infra.k8s.wlkr.ch](https://auth.infra.k8s.wlkr.ch) as
    `akadmin`, then create the four groups under *Directory &rarr; Groups*
    and add yourself — see
    [Authentik &rarr; Groups and roles](platform/authentik.md#groups-and-roles):

    | Group | Grants |
    | --- | --- |
    | `argocd-admins` | ArgoCD `role:admin` |
    | `argocd-viewers` | ArgoCD `role:readonly` |
    | `grafana-admins` | Grafana `Admin` |
    | `grafana-editors` | Grafana `Editor` |

    ArgoCD's `policy.default` is empty, so an SSO login that lands on an
    ArgoCD with no applications means a missing group, not a broken
    integration. Then create a personal account in *Directory &rarr; Users*,
    put it in the groups, and keep `akadmin` and its `bootstrap-password` in
    OpenBao as the break-glass identity — see
    [When Authentik is down](platform/authentik.md#when-authentik-is-down).

## Single-node clusters

The [documented layout](architecture/index.md#cluster-layout) has dedicated
workers, so the control-plane taint stays. On a single node:

- Remove the taint any time after step 8 and **before step 9**. Cilium
  tolerates the taint, but ArgoCD does not, so `make install-argo` would wait
  on unschedulable pods until Helm's `--wait` times out:

    ```bash
    make untaint
    ```

- Set `redis-ha.enabled: false` in `payload/argocd/values.yaml`; its hard
  per-host anti-affinity leaves pods Pending on one node whatever the taints
  say.
- Re-apply the taint when you later add worker nodes:

    ```bash
    make taint
    ```

## Reprovisioned nodes

The installer wipes the disk before `flatcar-install` runs, so a node armed
with `make reinstall` hands Ceph an empty disk. If that wipe ever fails, the
symptom is no `rook-ceph-osd` pods, a `CephCluster` still reporting `Ready`,
and `openbao-0` Pending on an unbound PVC at step 11 — see
[Rook-Ceph &rarr; No OSDs after reprovisioning](platform/rook-ceph.md#no-osds-after-reprovisioning).

## Verifying the result

The five commands that answer "is it actually fine?":

```bash
kubectl get nodes                                  # all Ready
kubectl -n argocd get applications                 # all Synced / Healthy
kubectl get certificate -A                         # READY=True
kubectl -n rook-ceph get cephcluster               # HEALTH_OK
make storage-check                                 # a PVC actually binds
```

The last one earns its place: `cephcluster` reports `Ready` on a cluster with
no OSDs and no CSI driver.

Once DNS points at the gateway IPs, the platform UIs are reachable — see
[Platform &rarr; HTTPRoute Locations](platform/index.md#httproute-locations).

## Troubleshooting PXE boot

Step 7 fails as a black screen with a blinking cursor and no error message.
Read the `make serve` log instead of the node and ask how far it got.

| Symptom | Likely cause |
| --- | --- |
| Nothing after the startup lines; the node never appears | DHCP isn't handing out options 66/67, or the node isn't on the same L2 segment. Check the DHCP lease and that PXE is enabled in firmware. |
| `PXE-E32: TFTP open timeout` | `make serve` isn't running, or a firewall is blocking UDP/69. On macOS, allow the Python interpreter through the firewall. |
| Bootloader loads, then "Could not find kernel image" or a hang at the menu | `boot_server_ip` in `inventory.yaml` is wrong. It is baked into the menu's kernel/initrd URLs. Fix it, re-run `make config`, and reboot the node. |
| `no generated menu has that MAC` in the boot server log | The node's `mac_address` in `inventory.yaml` doesn't match its actual NIC. The log prints the MAC the node actually asked for; put that in the inventory and re-run `make config`. |
| `collecting its boot menu -- booting from its local disk` | The node is not armed. `make reinstall LIMIT=<node>` and boot it again. Expected after an install: the boot server disarms a node once it has the OS image. |
| Kernel boots, then Ignition fails | The node couldn't fetch `ignition-<host>-install.json` over HTTP (port 8000), or the Butane template references an SSH key path that doesn't exist. |
| `refusing to wipe active disk` on the first boot after an install | The installer's disk stanza reached the installed system. `wipe_table` belongs in `ignition-<host>-install.json` only — see [Wiping the disk](architecture/boot-process.md#wiping-the-disk). |
| Node installs but never joins the cluster | Sysext download failed, or the kubeadm systemd unit errored. SSH in as `core` and check `journalctl -u kubeadm`. |

A machine with two NICs PXE boots from whichever it likes, and the MAC on the
case is often not that one. A node that boots the menu and stalls right after
is almost always a wrong `boot_server_ip`, not a broken image.

The boot server serves `output/tftp` over TFTP and `output/http` over HTTP —
see [Boot & Bootstrap Process](architecture/boot-process.md). If a file is
missing from those directories, re-run `make artifacts`.
