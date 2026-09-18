.PHONY: setup artifacts download config serve clean clean-artifacts kubeconfig untaint taint fonts fonts-check bootstrap install-cilium install-argo bootstrap-apps storage-check reinstall reinstall-cancel bao-init bao-unseal bao-secrets

chart_version = $(shell awk '/chart:/{f=1} f&&/targetRevision:/{print $$2; exit}' $(1))
CILIUM_VERSION      := $(call chart_version,payload/platform/cilium/application.yaml)
ARGOCD_VERSION      := $(call chart_version,payload/argocd/application.yaml)
GATEWAY_API_VERSION := $(shell awk '/repoURL:.*gateway-api/{f=1} f&&/targetRevision:/{print $$2; exit}' payload/platform/gateway-api-crds/application.yaml)

require = @test -n "$($(1))" || { echo "ERROR: $(1) is empty -- could not read a version from $(2)"; exit 1; }

setup:
	uv sync
	@echo "Virtual environment created."

artifacts: download config

download:
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/download.yaml

config:
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/config.yaml

serve:
	sudo $$(uv python find) boot_server/serve.py

kubeconfig:
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/kubeconfig.yaml
	@echo "Kubeconfig saved to output/kubeconfig"

untaint:
	@echo "WARNING: Only run this task in a single node cluster setup!"
	kubectl taint nodes --all node-role.kubernetes.io/control-plane-

# --overwrite: without it this fails on any node that is already tainted, so
# re-tainting a partly untainted cluster takes two tries and a reading of the
# error message.
taint:
	@echo "Re-applying control-plane taints..."
	kubectl taint nodes --overwrite \
		-l node-role.kubernetes.io/control-plane \
		node-role.kubernetes.io/control-plane:NoSchedule

fonts:
	scripts/update-fonts.sh

fonts-check:
	scripts/update-fonts.sh --check

# Everything ArgoCD needs to run, and nothing it can install itself.
bootstrap: install-cilium install-argo bootstrap-apps

# ServiceMonitors off: their CRDs arrive with the prometheus-operator-crds
# Application in 01-crds, and ArgoCD adds the monitors when it adopts the
# release. The flags leave cilium-config untouched.
install-cilium:
	$(call require,GATEWAY_API_VERSION,payload/platform/gateway-api-crds/application.yaml)
	$(call require,CILIUM_VERSION,payload/platform/cilium/application.yaml)
	@echo "Installing Gateway API CRDs ($(GATEWAY_API_VERSION))..."
	kubectl apply --server-side \
		-f https://github.com/kubernetes-sigs/gateway-api/releases/download/$(GATEWAY_API_VERSION)/standard-install.yaml
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

install-argo:
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
bootstrap-apps:
	@echo "Handing the cluster over to ArgoCD..."
	kubectl apply -f payload/platform/argocd-projects/projects.yaml
	kubectl apply -f payload/argocd/application.yaml
	@echo "AppProjects and the self-managing argocd Application created."
	@echo "ArgoCD now syncs the platform ApplicationSet and everything under it."
	@echo "It stops at 05-secrets until make bao-init and make bao-unseal run."

storage-check:
	scripts/storage-check.sh

bao-init:
	scripts/bao-init.sh

bao-unseal:
	scripts/bao-unseal.sh

bao-secrets:
	scripts/bao-secrets.sh

reinstall:
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/reinstall.yaml $(if $(LIMIT),--limit "$(LIMIT)")

reinstall-cancel:
	uv run ansible-playbook -i ansible/inventory.yaml ansible/playbooks/reinstall.yaml -e pxe_default=localboot $(if $(LIMIT),--limit "$(LIMIT)")

clean:
	@printf '%s\n' \
	  '' \
	  '  make clean deletes everything under output/, including credentials that' \
	  '  are generated once and never regenerated identically:' \
	  '' \
	  '    output/credentials/openbao-init.json  the 5 OpenBao unseal keys and root' \
	  '                                          token. Without these every secret' \
	  '                                          the cluster holds is unrecoverable.' \
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

clean-artifacts:
	rm -rf output/http output/tftp output/tmp
	@echo "Downloaded and generated artifacts removed. output/credentials/ kept."
