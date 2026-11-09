# Quickstart

![Homelab Logo](<https://homelab.wlkr.ch/assets/images/logo.png>)

Bring up the bare metal Kubernetes cluster from scratch, using Flatcar Container Linux and Kubeadm. The steps are thirteen commands; set aside an afternoon anyway, because step 7 depends on your DHCP server behaving.

> [!WARNING]
>
> **Read this first if the cluster isn't mine**
>
> Node names, IP addresses, the `wlkr.ch` domain, and the Git repository URL are hardcoded throughout `payload/`. Following the steps below verbatim gives you a cluster whose ArgoCD syncs from **this** repository and whose certificates are issued for a domain you don't control. Work through [Adapting This for Your Cluster](<https://homelab.wlkr.ch/adapting/index.md>) **before** step 1.

## Hardware Requirements

Each node must have:

- A NIC that supports PXE booting
- An NVMe drive, or a different `install_disk` in `inventory.yaml`
- Enough disk for Flatcar and the root filesystem. On workers the root partition is capped (`size_mib` in `ansible/templates/butane_node_config.yaml.j2`) and Rook-Ceph takes the remainder as OSD storage; control planes keep the whole disk
- At least 4 GB of RAM: the installer runs from a RAM disk and streams the Flatcar image straight to disk

The deployment host (the machine running Ansible and the boot server) must be on the same L2 network segment as the nodes.

## Prerequisites

### Tools on the deployment host

| Tool | Used by | Install |
| --- | --- | --- |
| `make` | every step | Xcode CLT / `build-essential` |
| `git` | cloning this repository | your package manager |
| `uv` | Ansible and the boot server | `curl -LsSf https://astral.sh/uv/install.sh \\| sh` |
| `butane` | transpiling Butane YAML to Ignition JSON | `brew install butane` or the [Flatcar docs](<https://www.flatcar.org/docs/latest/provisioning/config-transpiler/>) |
| `gpg` and `gpgv` | checking the signatures on the Flatcar and syslinux checksums in `make download` | `brew install gnupg` or your package manager |
| `kubectl` | steps 8 onwards | [kubernetes.io](<https://kubernetes.io/docs/tasks/tools/>) |
| `helm` | `make install-cilium`, `make install-argo` | [helm.sh](<https://helm.sh/docs/intro/install/>) |

### Other requirements

- **SSH Key**: An Ed25519 key at `~/.ssh/id_ed25519.pub` (or edit `ansible/templates/butane_node_config.yaml.j2` and `butane_installer_config.yaml.j2` to use a different path/key)
- **External DHCP Server**: Must point PXE clients at the deployment host and give every node a fixed address:
- Option 66 (`next-server`): IP of the machine running `make serve`
- Option 67 (`filename`): `lpxelinux.0` for BIOS, `syslinux.efi` for UEFI
- A reservation per `mac_address` handing out exactly that host's `ansible_host`. Nothing in the node config sets a static address; the API server's `advertiseAddress`, the etcd peer URLs kubeadm derives from it and the metrics listeners are all rendered from `ansible_host`, so a node that leases a different address joins with the wrong one or not at all.

> [!TIP]
>
> **Consumer routers and PXE**
>
> Many home routers accept options 66 and 67 and then serve neither. If step 7 produces silence in the boot server log, prove the DHCP side first with `tcpdump -i <iface> port 67 or port 68`.

## Setup

1. **Clone Repository**:

   ```bash
   git clone https://github.com/JanWelker/homelab.git homelab
   cd homelab
   ```
2. **Configure Inventory**: Edit `ansible/inventory.yaml` to define your target nodes and settings.

   Every variable, and the `make` target that bakes it into `output/`, so you know what to re-run after an edit:

   | Variable | Why it matters | Read by |
   | --- | --- | --- |
   | `boot_server_ip` | **Most commonly missed.** Baked into the generated PXE menu as the URL for the kernel, initrd, and Ignition config. If this isn't the IP of the machine that will run `make serve`, nodes load the bootloader and then hang. | `make config`, `make serve` |
   | `mac_address` (per host) | Selects which generated PXE menu a node picks up | `make config`, `make reinstall`, `make serve` |
   | `ansible_host` (per host) | The address the node's DHCP reservation hands out; rendered into the kubeadm and static pod configs | `make config`, `make reinstall` |
   | `install_disk` | Target disk for the Flatcar install, overridable per host for a node whose disk sits elsewhere. **Wiped completely** — partitions, GPT and a device-level discard — before the install | `make config` |
   | `control_plane_vip`, `control_plane_vip_interface` | The API endpoint kubeadm bakes into every certificate and kubeconfig, and the NIC kube-vip advertises it on; see [Control Plane VIP](<https://homelab.wlkr.ch/operations/control-plane-vip/index.md>) | `make config`; the VIP also `make kubeconfig`, `make reinstall` |
   | `control_plane_endpoint_host` | The name or address nodes join through; a name only on a fresh build, see [Endpoint name](<https://homelab.wlkr.ch/operations/control-plane-vip/#endpoint-name>) | `make config`, `make kubeconfig`, `make reinstall` |
   | `pod_subnet`, `service_subnet` | kubeadm's cluster networks; change only on a collision with your LAN | `make config` |
   | `router_ip`, `router_asn`, `cluster_asn` | The eBGP peer the Gateway addresses are announced to; rendered into `output/router/frr-bgp.conf` for the router, and `payload/platform/cilium/bgp.yaml` must say the same; see [Cilium](<https://homelab.wlkr.ch/platform/cilium/#configuration>) | `make config` |
   | `flatcar_channel`, `flatcar_version`, `syslinux_version` | The OS image and bootloader to fetch; `flatcar_version` also names the image the installer writes | `make download`; `flatcar_version` also `make config` |
   | `kubernetes_version` | The sysext image to fetch, and the version kubeadm and sysupdate are rendered with | `make download`, `make config` |
   | `kube_vip_version` | The bootstrap static pod only; the running image is pinned in `payload/platform/kube-vip/`; see [Inventory](<https://homelab.wlkr.ch/architecture/ansible/#inventory>) | `make config` |
   | `bootstrap_cluster` (`control_plane` group) | `true` for this first install, so the first control-plane node runs `kubeadm init`; back to `false` once the cluster is up — see [Inventory](<https://homelab.wlkr.ch/architecture/ansible/#inventory>) | `make config`, `make reinstall` |
   | `control_plane` / `workers` membership | Decides the node role, and with it the partition layout and the kubeadm command | `make config` |
   | `ansible_user`, `ansible_ssh_common_args` (`k8s_nodes` group) | How the playbooks reach a running node for the kubeconfig and the join-credential renewal | `make kubeconfig`, `make config` |
3. **Initialize Environment**: Creates the `uv` virtual environment and installs dependencies:

   ```bash
   make setup
   ```
4. **Download Artifacts**: Fetches Flatcar, Syslinux and the Kubernetes sysext image into `output/http`:

   ```bash
   make download
   ```
5. **Generate Configurations**:

   ```bash
   make config
   ```

   Writes Ignition configs to `output/http` and PXE menus to `output/tftp`. Each host gets `ignition-<host>-install.json`, which the PXE environment runs to wipe the disk and install, and `ignition-<host>.json`, which `flatcar-install` embeds into the installed system — see [Boot &amp; Bootstrap Process](<https://homelab.wlkr.ch/architecture/boot-process/index.md>).

   Re-run this after **any** change to `inventory.yaml`; the values are baked into the generated files. `make artifacts` runs steps 4 and 5 together. Check that one PXE menu was generated per node:

   ```bash
   ls output/tftp/pxelinux.cfg/     # one 01-<mac> file per host
   ```
6. **Start Boot Server**:

   ```bash
   make serve
   ```

   Leave this running through step 7 and keep the window visible: it names each node and logs one line per request. Its opening lines say what each node will do when powered on: a boxed `ARMED TO INSTALL` warning names the nodes that will wipe and install, and a node under *booting from disk* will not, however often you reboot it.

   ```console
     ⚠  ARMED TO INSTALL: odin, thor

        Every disk above is wiped on boot -- the partition table, every
        partition on it, and the Ceph OSD with them. No data survives, and
        nothing asks for confirmation at the console.

        To cancel, run:  make reinstall-cancel
                         make reinstall-cancel LIMIT=<node>  for one

   20:33:04  server                                                    booting from disk: freya, heimdall, loki, valkyrie
   20:34:17  control-plane odin     10.9.2.1        fc:3f:db:0d:8f:5c  collecting its boot menu -- armed, so it will install
   20:34:48  control-plane odin     10.9.2.1        fc:3f:db:0d:8f:5c  collecting the OS image (1.2 GB) -- this is the long one
   20:36:12  control-plane odin     10.9.2.1        fc:3f:db:0d:8f:5c  OS image delivered -- switching to local boot, so the reboot lands on the disk
   ```
7. **Arm the install, then boot the machines**:

   ```bash
   make reinstall
   ```

   The generated PXE configs default to booting the local disk; `make reinstall` flips them to `Install`, for `LIMIT=<node>` or, after a typed confirmation, for every host. The boot server disarms a node itself once it has delivered the OS image, so the reboot at the end of the install lands on the disk — see [Switching back to local boot](<https://homelab.wlkr.ch/architecture/boot-process/#switching-back-to-local-boot>).

   Then power on the nodes. No menu appears; each node does what it was armed to do, and the boot server log walks it through the fetches, then `switching to local boot`, then after the reboot the Ignition config again plus the sysext images. A node still shown by bare IP has not fetched a menu the server recognises.

   - **Leave the boot server running until every node is up.** The sysexts are fetched on the first boot from disk.
   - The cluster comes up `NotReady` because no CNI is installed. This is correct; do not fix it yet.
   - If a node stalls, see [Troubleshooting PXE boot](<#troubleshooting-pxe-boot>).

   > **Watching an install**
   >
   > `journalctl -u flatcar-install -f` as `core@<node>` (`flatcar-install-uefi` on UEFI firmware) shows the wipe and the write (so does a monitor on the node). A failed install does *not* reboot — the node stays in the PXE environment with the journal intact.
8. **Retrieve Kubeconfig**: Once the control plane node responds to SSH:

   ```bash
   make kubeconfig
   ```

   Every `make` target that touches the cluster reads `output/kubeconfig` (`make KUBECONFIG=<file> <target>` picks another); only `kubectl` in your own shell needs the export. Verify the cluster answers. `NotReady` is expected at this point:

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

   Installs the Gateway API CRDs, then Cilium via Helm — only what ArgoCD needs in order to run; everything else arrives through ArgoCD, at the versions pinned in each component's `application.yaml` (see [GitOps Strategy → Version pins](<https://homelab.wlkr.ch/architecture/gitops/#version-pins>) and [Cilium → Installation](<https://homelab.wlkr.ch/platform/cilium/#installation>)). `make bootstrap` runs this step, `make install-argo` and `make bootstrap-apps` in one go.

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

      There is no way into the UI yet: the local admin is disabled in `payload/argocd/values.yaml` and Authentik arrives later through GitOps. Follow the bootstrap with `kubectl` instead.

      **Tip — the UI before Authentik exists:** re-enable the local admin with a patch. It holds only until the next step: right now ArgoCD is the plain Helm release `make install-argo` made, and `make bootstrap-apps` hands it to the self-healing `argocd` Application, whose first sync puts `admin.enabled` back to what `payload/argocd/values.yaml` says. After that the only way in is the pull request in [Authentik → When Authentik is down](<https://homelab.wlkr.ch/platform/authentik/#when-authentik-is-down>). The restart is what generates the password Secret:

      ```bash
      kubectl -n argocd patch cm argocd-cm --type merge \
        -p '{"data":{"admin.enabled":"true"}}'
      kubectl -n argocd rollout restart deploy/argocd-server
      kubectl -n argocd rollout status deploy/argocd-server

      kubectl -n argocd get secret argocd-initial-admin-secret \
        -o jsonpath='{.data.password}' | base64 -d; echo
      kubectl -n argocd port-forward svc/argocd-server 8080:80   # http://localhost:8080, user admin
      ```

      Nothing to undo: the handover reverts the patch.
    - **Hand the cluster over to ArgoCD**:

      ```bash
      make bootstrap-apps
      ```

      Applies the AppProjects, then the self-managing `argocd` Application, which brings the `platform` ApplicationSet and with it every component at once. They converge in the [order their dependencies allow](<https://homelab.wlkr.ch/platform/#rollout-order>); watch them settle:

      ```bash
      kubectl -n argocd get applications -w
      ```

      **Everything that reads a secret stays Degraded, and that is expected.** No `ExternalSecret` resolves until step 11 — see [Bootstrap pauses at OpenBao](<https://homelab.wlkr.ch/architecture/gitops/#bootstrap-pauses-at-openbao>).
    - **Gate on storage** before trusting anything that mounts a volume:

      ```bash
      make storage-check
      ```

      A `StorageClass` exists whether or not a CSI driver registered for it, so `rook-ceph-cluster` can go Healthy with no working provisioner. This asks for a volume the way a workload would and names the first broken link — see [Rook-Ceph → Is storage ready?](<https://homelab.wlkr.ch/platform/rook-ceph/#is-storage-ready>).
11. **Initialise the secret store**: OpenBao starts uninitialised, sealed and empty; every `ExternalSecret` stays Degraded until it is unsealed and `certificates` until it is populated, then everything converges on its own.

    ```bash
    make bao-init
    ```

    Initialises and unseals OpenBao, enables the `kv` v2 engine and the Kubernetes auth method, and writes the policy and role External Secrets authenticates with, then replaces the root token with an operator login. The keys land in `output/credentials/openbao-init.json`, the login in `openbao-operator.json` beside it.

    > **Move those keys before you do anything else**
    >
    > That file is a plaintext copy of the keys to every secret the cluster holds. Copy them into a password manager and delete it. Losing them means the data is unrecoverable. See [OpenBao → Bootstrap](<https://homelab.wlkr.ch/platform/openbao/#bootstrap>).

    Then populate the paths the cluster reads:

    ```bash
    make bao-secrets
    ```

    It prompts, with input hidden, for every value that belongs to an account outside the cluster: the DNS, mail and other third-party logins listed at the top of `scripts/bao-secrets.sh`, each against a path in [OpenBao → KV layout](<https://homelab.wlkr.ch/platform/openbao/#kv-layout>). A required value has no skip; an optional one is issued by something that runs on the cluster and takes Enter until it exists. A fork without one of those accounts removes its prompt from the script. Everything random — `kv/authentik/config` with the OIDC client credentials ArgoCD and Grafana read back, Grafana's break-glass admin password — is not prompted for: a `PushSecret` writes it once the store validates, see [Generated secrets](<https://homelab.wlkr.ch/platform/openbao/#generated-secrets>). On a running cluster it asks per path before overwriting — see [Rotating a credential](<https://homelab.wlkr.ch/platform/openbao/#rotating-a-credential>).

    > **Paste them at the prompt, not onto a command line**
    >
    > A secret containing `#` on a command line is truncated at it, and one containing `!` is mangled by history expansion — both silently. Non-interactively, the matching environment variables are honoured when already set; quote them with **single** quotes.

    Once the store validates, every `ExternalSecret` resolves, `certificates` issues the gateway certificates and the Gateways come up. The store is re-checked every few minutes, so that can take that long to start.
12. **Create the first administrator**: Authentik ships the built-in `akadmin` account, and the blueprints switch it off as soon as it exists — see [Bootstrap credentials](<https://homelab.wlkr.ch/platform/authentik/#bootstrap-credentials>). Switch it on, and read its password back out of OpenBao (needs a token — `bao login -method=userpass username=operator` inside the pod, or the port-forward in [OpenBao → Authenticating locally](<https://homelab.wlkr.ch/platform/openbao/#authenticating-locally>)):

    ```bash
    kubectl -n authentik exec deploy/authentik-server -c server -- ak shell -c \
      'from authentik.core.models import User; User.objects.filter(username="akadmin").update(is_active=True)'
    kubectl -n openbao exec openbao-0 -- \
      bao kv get -mount=kv -field=bootstrap-password authentik/config
    ```

    Log in at [auth.k8s.wlkr.ch](<https://auth.k8s.wlkr.ch>) as `akadmin`; the first login enrols a second factor. Create a personal account in *Directory → Users* and put it in `authentik Admins` and the groups listed in [Authentik → Groups and roles](<https://homelab.wlkr.ch/platform/authentik/#groups-and-roles>) under *Directory → Groups*; the blueprints create them.

    ArgoCD's `policy.default` is empty, so an SSO login that lands on an ArgoCD with no applications means a missing group, not a broken integration. Then switch `akadmin` off again with the same command and `is_active=False`; when Authentik itself is unavailable, see [When Authentik is down](<https://homelab.wlkr.ch/platform/authentik/#when-authentik-is-down>).

## Single-node clusters

The [documented layout](<https://homelab.wlkr.ch/architecture/#cluster-layout>) has dedicated workers, so the control-plane taint stays. On a single node:

- Remove the taint once `make kubeconfig` has answered and **before `make install-argo`**. Cilium tolerates the taint, but ArgoCD does not, so `make install-argo` would wait on unschedulable pods until Helm's `--wait` times out:

  ```bash
  make untaint
  ```
- Set `redis-ha.enabled: false` in `payload/argocd/values.yaml`; its hard per-host anti-affinity leaves pods Pending on one node whatever the taints say.
- Re-apply the taint when you later add worker nodes:

  ```bash
  make taint
  ```

## Reprovisioned nodes

The installer wipes the disk before `flatcar-install` runs, so a node armed with `make reinstall` hands Ceph an empty disk. If that wipe ever fails, the symptom is no `rook-ceph-osd` pods, a `CephCluster` still reporting `Ready`, and `openbao-0` Pending on an unbound PVC at step 11 — see [Rook-Ceph → No OSDs after reprovisioning](<https://homelab.wlkr.ch/platform/rook-ceph/#no-osds-after-reprovisioning>).

## Verifying the result

The five commands that answer "is it actually fine?":

```bash
kubectl get nodes                                  # all Ready
kubectl -n argocd get applications                 # all Synced / Healthy
kubectl get certificate -A                         # READY=True
kubectl -n rook-ceph get cephcluster               # HEALTH_OK
make storage-check                                 # a PVC actually binds
```

The last one earns its place: `cephcluster` reports `Ready` on a cluster with no OSDs and no CSI driver.

Once DNS points at the gateway IPs, the platform UIs are reachable — see [Platform → HTTPRoute Locations](<https://homelab.wlkr.ch/platform/#httproute-locations>).

## Troubleshooting PXE boot

Step 7 fails as a black screen with a blinking cursor and no error message. Read the `make serve` log instead of the node and ask how far it got.

| Symptom | Likely cause |
| --- | --- |
| Nothing after the startup lines; the node never appears | DHCP isn't handing out options 66/67, or the node isn't on the same L2 segment. Check the DHCP lease and that PXE is enabled in firmware. |
| Node comes up on an address other than its `ansible_host` | No per-MAC DHCP reservation, or one for the wrong NIC. The join uses the rendered address, not the leased one. |
| `PXE-E32: TFTP open timeout` | `make serve` isn't running, or a firewall is blocking UDP/69. On macOS, allow the Python interpreter through the firewall. |
| Bootloader loads, then "Could not find kernel image" or a hang at the menu | `boot_server_ip` in `inventory.yaml` is wrong. It is baked into the menu's kernel/initrd URLs. Fix it, re-run `make config`, and reboot the node. |
| `no generated menu has that MAC` in the boot server log | The node's `mac_address` in `inventory.yaml` doesn't match its actual NIC. The log prints the MAC the node actually asked for; put that in the inventory and re-run `make config`. |
| `collecting its boot menu -- booting from its local disk` | The node is not armed. `make reinstall LIMIT=<node>` and boot it again. Expected after an install: the boot server disarms a node once it has the OS image. |
| Kernel boots, then Ignition fails | The node couldn't fetch `ignition-<host>-install.json` over HTTP (port 8000), or the Butane template references an SSH key path that doesn't exist. |
| `refusing to wipe active disk` on the first boot after an install | The installer's disk stanza reached the installed system. `wipe_table` belongs in `ignition-<host>-install.json` only — see [Wiping the disk](<https://homelab.wlkr.ch/architecture/boot-process/#wiping-the-disk>). |
| Node installs but never joins the cluster | Sysext download failed, or the join unit errored. SSH in as `core` and check `journalctl -u bootstrap-k8s`. |

A machine with two NICs PXE boots from whichever it likes, and the MAC on the case is often not that one. A node that boots the menu and stalls right after is almost always a wrong `boot_server_ip`, not a broken image.

The boot server serves `output/tftp` over TFTP and `output/http` over HTTP — see [Boot &amp; Bootstrap Process](<https://homelab.wlkr.ch/architecture/boot-process/index.md>). If a file is missing from those directories, re-run `make artifacts`.
