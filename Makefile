.PHONY: help setup artifacts download config ignition-check serve clean clean-artifacts kubeconfig check-context untaint taint fonts fonts-check mermaid mermaid-check bootstrap install-cilium install-argo bootstrap-apps storage-check reinstall reinstall-cancel reprovision node-drift reboot-all kube-upgrade uptime os-release bao-init bao-unseal bao-policies bao-secrets bao-rotate-root claude-session-pat

# Every target that touches the cluster reads the kubeconfig make kubeconfig
# wrote, so a bootstrap never lands in whatever ~/.kube/config points at.
# Override per call: make KUBECONFIG=~/.kube/config bao-unseal
KUBECONFIG ?= $(CURDIR)/output/kubeconfig
export KUBECONFIG

chart_version = $(shell awk '/chart:/{f=1} f&&/targetRevision:/{print $$2; exit}' $(1))
CILIUM_VERSION      := $(call chart_version,payload/platform/cilium/application.yaml)
ARGOCD_VERSION      := $(call chart_version,payload/argocd/application.yaml)
GATEWAY_API_VERSION := $(shell awk '/repoURL:.*gateway-api/{f=1} f&&/targetRevision:/{print $$2; exit}' payload/platform/gateway-api-crds/application.yaml)
GATEWAY_API_SHA256  := $(shell awk '/standard-install.yaml sha256:/{sub(/.*sha256:/,""); print; exit}' payload/platform/gateway-api-crds/application.yaml)

.DEFAULT_GOAL := help

require = @test -n "$($(1))" || { echo "ERROR: $(1) is empty -- could not read a version from $(2)"; exit 1; }

help: ## Show this list
	@awk 'BEGIN{FS=":.*## "} /^##@ /{printf "\n%s\n", substr($$0,5)} /^[a-z0-9-]+:.*## /{printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

##@ Artifacts and boot server
setup: ## Create the Python virtual environment
	uv sync
	@echo "Virtual environment created."

artifacts: download config ## Run download and config

download: ## Download the Flatcar image, kernel, initrd and sysexts
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/download.yaml

config: ## Render Ignition, PXE menus and credentials into output/
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/config.yaml

ignition-check: ## Validate every host's Ignition and kubeadm configs
	scripts/ignition-check.sh

serve: ## Run the PXE boot server
	.venv/bin/boot-server

##@ Cluster access
kubeconfig: ## Fetch the admin kubeconfig to output/kubeconfig
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/kubeconfig.yaml
	@echo "Kubeconfig saved to output/kubeconfig"

check-context: ## Show which kubeconfig and context make uses
	@test -f "$(KUBECONFIG)" || { echo "ERROR: $(KUBECONFIG) not found -- run make kubeconfig first, or pass KUBECONFIG=<file>"; exit 1; }
	@echo "Using context $$(kubectl config current-context) from $(KUBECONFIG)"

untaint: check-context ## Allow workloads on control planes (single-node only)
	@echo "WARNING: Only run this task in a single node cluster setup!"
	kubectl taint nodes --all node-role.kubernetes.io/control-plane-

# --overwrite: without it this fails on any node that is already tainted, so
# re-tainting a partly untainted cluster takes two tries and a reading of the
# error message.
taint: check-context ## Re-apply the control-plane taints
	@echo "Re-applying control-plane taints..."
	kubectl taint nodes --overwrite \
		-l node-role.kubernetes.io/control-plane \
		node-role.kubernetes.io/control-plane:NoSchedule

##@ Docs assets
fonts: ## Update the vendored docs fonts
	scripts/update-fonts.sh

fonts-check: ## Check the vendored docs fonts are current
	scripts/update-fonts.sh --check

mermaid: ## Update the vendored mermaid.js
	scripts/update-mermaid.sh

mermaid-check: ## Check the vendored mermaid.js is current
	scripts/update-mermaid.sh --check

##@ Bootstrap
# Everything ArgoCD needs to run, and nothing it can install itself.
bootstrap: install-cilium install-argo bootstrap-apps ## Install Cilium and ArgoCD and hand over to ArgoCD

# ServiceMonitors off: their CRDs arrive with the prometheus-operator-crds
# Application, and ArgoCD adds the monitors when it adopts the release. The
# flags leave cilium-config untouched.
install-cilium: check-context ## Install the Gateway API CRDs and Cilium
	$(call require,GATEWAY_API_VERSION,payload/platform/gateway-api-crds/application.yaml)
	$(call require,GATEWAY_API_SHA256,payload/platform/gateway-api-crds/application.yaml)
	$(call require,CILIUM_VERSION,payload/platform/cilium/application.yaml)
	@echo "Installing Gateway API CRDs ($(GATEWAY_API_VERSION))..."
	@mkdir -p output
	curl -fsSL -o output/standard-install.yaml https://github.com/kubernetes-sigs/gateway-api/releases/download/$(GATEWAY_API_VERSION)/standard-install.yaml
	echo "$(GATEWAY_API_SHA256)  output/standard-install.yaml" | shasum -a 256 -c -
	kubectl apply --server-side -f output/standard-install.yaml
	helm upgrade --install cilium cilium \
		--repo https://helm.cilium.io/ \
		--version $(CILIUM_VERSION) \
		--namespace kube-system \
		--values payload/platform/cilium/values.yaml \
		--set prometheus.serviceMonitor.enabled=false \
		--set operator.prometheus.serviceMonitor.enabled=false \
		--set hubble.metrics.serviceMonitor.enabled=false
	@echo "Waiting for Cilium to be ready..."
	kubectl -n kube-system rollout status ds/cilium

install-argo: check-context ## Install ArgoCD
	$(call require,ARGOCD_VERSION,payload/argocd/application.yaml)
	helm upgrade --install argocd argo-cd \
		--repo https://argoproj.github.io/argo-helm \
		--namespace argocd \
		--create-namespace \
		--values payload/argocd/values.yaml \
		--version $(ARGOCD_VERSION) \
		--wait

# The AppProjects go first: an Application naming a project that does not
# exist is rejected, and the argocd Application names one.
bootstrap-apps: check-context ## Apply the AppProjects and the argocd Application
	@echo "Handing the cluster over to ArgoCD..."
	kubectl apply -f payload/platform/argocd-projects/projects.yaml
	kubectl apply -f payload/argocd/application.yaml
	@echo "AppProjects and the self-managing argocd Application created."
	@echo "ArgoCD now syncs the platform ApplicationSet and everything under it."
	@echo "Every ExternalSecret stays Degraded until make bao-init and make bao-unseal run."

storage-check: check-context ## Provision a test PVC on Ceph and clean it up
	scripts/storage-check.sh

##@ OpenBao
bao-init: check-context ## Initialise OpenBao and save the unseal keys
	scripts/bao-init.sh

bao-unseal: check-context ## Unseal every OpenBao pod
	scripts/bao-unseal.sh

bao-policies: check-context ## Apply the OpenBao policies and auth roles
	scripts/bao-policies.sh

bao-secrets: check-context ## Write the typed secrets into OpenBao
	scripts/bao-secrets.sh

# Once, on a cluster initialised before bao-init revoked its root token.
bao-rotate-root: check-context ## Replace an old bao-init's root token with the operator login
	scripts/bao-rotate-root.sh

# make claude-session-pat SESSION=homelab [DELETE=1]
claude-session-pat: check-context ## Store or delete a session's GitHub PAT (SESSION=, DELETE=1)
	@test -n "$(SESSION)" || { echo "usage: make claude-session-pat SESSION=<session> [DELETE=1]"; exit 2; }
	scripts/claude-session-pat.sh $(if $(DELETE),--delete) $(SESSION)

##@ Nodes
# Without LIMIT this arms every node, and the firmware boots network-first: a
# power cut after that reinstalls all three control planes and every Ceph OSD.
# make clean asks before deleting the credentials; this asks before that.
reinstall: ## Arm nodes to reinstall on next network boot (LIMIT=)
	@if [ -z "$(LIMIT)" ]; then \
	  printf '%s\n' \
	    '' \
	    '  make reinstall without LIMIT arms EVERY node. Each one wipes its disk,' \
	    '  Ceph OSD included, the next time it boots from the network -- and the' \
	    '  firmware boots from the network first, so a power cut is enough.' \
	    '' \
	    '  To rebuild one node:  make reinstall LIMIT=<node>' \
	    ''; \
	  if [ ! -t 0 ]; then \
	    echo "  not a terminal -- pass LIMIT=<node>, or re-run interactively to confirm"; \
	    exit 1; \
	  fi; \
	  printf '  Arm all nodes for reinstall? [y/N] '; \
	  read -r reply; \
	  case "$$reply" in \
	    [yY]|[yY][eE][sS]) ;; \
	    *) echo "  cancelled -- nothing armed."; exit 1;; \
	  esac; \
	fi
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/reinstall.yaml $(if $(LIMIT),--limit "$(LIMIT)")

reinstall-cancel: ## Disarm a pending reinstall (LIMIT=)
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/reinstall.yaml -e pxe_default=localboot $(if $(LIMIT),--limit "$(LIMIT)")

# Flatcar has no Python, so only the raw module works. LIMIT=<node|group>.
NODES = ANSIBLE_DEPRECATION_WARNINGS=False uv run ansible $(if $(LIMIT),$(LIMIT),k8s_nodes) -i ansible/inventory.yaml -m ansible.builtin.raw
# Folds the "<host> | CHANGED | rc=0 >>" header and its output into one line.
ONELINE = awk '/^[a-z0-9-]+ \| [A-Z]+/{h=""; if ($$3=="CHANGED") h=$$1; else print $$1": "$$3; next} \
	h&&NF{sub(/^ +/,""); print h": "$$0; h=""}' | sort -V

# flatcar-reset with the current template, keeping etcd, Ceph and kubelet
# state; Kured reboots. See docs/operations/nodes.md#reprovisioning-a-node
reprovision: config ## Re-run Ignition on nodes through flatcar-reset (LIMIT=, ALLOW_MINOR=1)
	@if [ -z "$(LIMIT)" ]; then \
	  printf '%s\n' \
	    '' \
	    '  make reprovision without LIMIT stages a reset on EVERY node. Kured reboots' \
	    '  them one at a time, and each runs Ignition again on that boot.' \
	    '' \
	    '  To reprovision one node:  make reprovision LIMIT=<node>' \
	    ''; \
	  if [ ! -t 0 ]; then \
	    echo "  not a terminal -- pass LIMIT=<node>, or re-run interactively to confirm"; \
	    exit 1; \
	  fi; \
	  printf '  Stage a reset on all nodes? [y/N] '; \
	  read -r reply; \
	  case "$$reply" in \
	    [yY]|[yY][eE][sS]) ;; \
	    *) echo "  cancelled -- nothing staged."; exit 1;; \
	  esac; \
	fi
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/reprovision.yaml $(if $(LIMIT),--limit "$(LIMIT)") \
	  -e allow_minor=$(if $(ALLOW_MINOR),true,false)

# Compares the checksum Ignition wrote with a fresh render.
node-drift: config ## List the nodes running an older Ignition config (LIMIT=)
	@$(NODES) -a 'cat /etc/node-config.sha256 2>/dev/null || printf none; echo' | $(ONELINE) | \
	  while read -r host have; do host=$${host%:}; \
	    want=$$(cat output/reprovision/$$host.sha256); \
	    if [ "$$have" = "$$want" ]; then echo "$$host: current"; \
	    elif [ "$$have" = none ]; then echo "$$host: no checksum, reprovision"; \
	    elif [ "$${have%%!*}" = UNREACHABLE ]; then echo "$$host: unreachable"; \
	    else echo "$$host: drifted, reprovision"; fi; \
	  done

# Sets the Kured sentinel everywhere; Kured's lock and alert gate decide the
# order, one node at a time. See docs/platform/kured.md
reboot-all: ## Ask Kured to reboot nodes one at a time (LIMIT=)
	@if [ -z "$(LIMIT)" ]; then \
	  printf '%s\n' \
	    '' \
	    '  make reboot-all asks Kured to reboot EVERY node, one at a time. Each' \
	    '  node that held an OpenBao pod waits for make bao-unseal before the next.' \
	    ''; \
	  if [ ! -t 0 ]; then \
	    echo "  not a terminal -- pass LIMIT=<node>, or re-run interactively to confirm"; \
	    exit 1; \
	  fi; \
	  printf '  Queue a reboot of all nodes? [y/N] '; \
	  read -r reply; \
	  case "$$reply" in \
	    [yY]|[yY][eE][sS]) ;; \
	    *) echo "  cancelled -- nothing queued."; exit 1;; \
	  esac; \
	fi
	@$(NODES) -a 'sudo touch /run/reboot-required && echo sentinel set' | $(ONELINE)

# After every control plane has rebooted onto a new Kubernetes patch; see
# docs/operations/upgrades.md#patch-releases
kube-upgrade: ## Upgrade the control plane to the patch its nodes booted
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/kube-upgrade.yaml

uptime: ## Show each node's uptime (LIMIT=)
	@$(NODES) -a 'uptime' | $(ONELINE)

os-release: ## Show each node's Flatcar and kernel version (LIMIT=)
	@$(NODES) -a '. /etc/os-release; echo "$$PRETTY_NAME, kernel $$(uname -r)"' | $(ONELINE)

##@ Cleanup
clean: ## Delete everything under output/, credentials included
	@printf '%s\n' \
	  '' \
	  '  make clean deletes everything under output/, including credentials that' \
	  '  are generated once and never regenerated identically:' \
	  '' \
	  '    output/credentials/openbao-init.json  the 5 OpenBao unseal keys. Without' \
	  '                                          these every secret the cluster' \
	  '                                          holds is unrecoverable.' \
	  '    output/credentials/openbao-operator.json  the OpenBao operator login.' \
	  '    output/credentials/encryption_key     decrypts the Secrets in etcd.' \
	  '    output/credentials/certificate_key    joins control-plane nodes.' \
	  '    output/credentials/kubeadm_token_*    joins worker nodes.' \
	  '    output/kubeconfig                     admin credential for the cluster.' \
	  '' \
	  '  make config writes NEW values for those, which a cluster already running' \
	  '  on the old ones will not accept.' \
	  '' \
	  '  Regenerable, and the only part worth cleaning:' \
	  '' \
	  '    output/http/   Flatcar image, kernel, initrd, sysexts   make download' \
	  '    output/tftp/   bootloader and PXE menus                 make config' \
	  '    output/tmp/    scratch space                            make config' \
	  '    output/router/ FRR BGP config for the router            make config' \
	  '' \
	  '  Copy output/credentials/ somewhere safe first, or run make clean-artifacts' \
	  '  to remove only the regenerable half.' \
	  ''
	@if [ ! -t 0 ]; then \
	  echo "  not a terminal -- re-run interactively, or use make clean-artifacts"; \
	  exit 1; \
	fi; \
	printf '  Delete output/credentials/ and everything else under output/? [y/N] '; \
	read -r reply; \
	case "$$reply" in \
	  [yY]|[yY][eE][sS]) rm -rf output/*; echo "  output/ emptied.";; \
	  *) echo "  cancelled -- nothing deleted.";; \
	esac

clean-artifacts: ## Delete the regenerable output/, keep credentials
	rm -rf output/http output/tftp output/tmp output/router
	@echo "Downloaded and generated artifacts removed. output/credentials/ kept."
