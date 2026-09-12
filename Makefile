.PHONY: download config serve clean kubeconfig untaint taint fonts fonts-check install-core install-cilium install-cert-manager install-argo bootstrap-apps

# Bootstrap component versions are not pinned here. Each one is read out of the
# ArgoCD Application that owns the component after the GitOps handover, so the
# release installed before ArgoCD exists is the same one ArgoCD then adopts.
# Renovate keeps those manifests current; there is nothing to bump in this file.
chart_version = $(shell awk '/chart:/{f=1} f&&/targetRevision:/{print $$2; exit}' $(1))

CILIUM_VERSION       := $(call chart_version,payload/platform/cilium/application.yaml)
CERT_MANAGER_VERSION := $(call chart_version,payload/platform/cert-manager/application.yaml)
ARGOCD_VERSION       := $(call chart_version,payload/argocd/application.yaml)
GATEWAY_API_VERSION  := $(shell awk '/repoURL:.*gateway-api/{f=1} f&&/targetRevision:/{print $$2; exit}' payload/platform/gateway-api/crds.yaml)

# Abort the target rather than handing Helm an empty --version if a manifest
# moves or changes shape.
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

taint:
	@echo "Re-applying control-plane taints..."
	kubectl taint nodes -l node-role.kubernetes.io/control-plane node-role.kubernetes.io/control-plane:NoSchedule

fonts:
	scripts/update-fonts.sh

fonts-check:
	scripts/update-fonts.sh --check

install-core: install-cilium install-cert-manager

install-cilium:
	$(call require,GATEWAY_API_VERSION,payload/platform/gateway-api/crds.yaml)
	$(call require,CILIUM_VERSION,payload/platform/cilium/application.yaml)
	-kubectl -n kube-system delete ds kube-proxy 2>/dev/null || true
	@echo "Installing Gateway API CRDs ($(GATEWAY_API_VERSION))..."
	kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/$(GATEWAY_API_VERSION)/standard-install.yaml
	helm repo add cilium https://helm.cilium.io/
	helm repo update
	helm upgrade --install cilium cilium/cilium \
		--version $(CILIUM_VERSION) \
		--namespace kube-system \
		--values payload/platform/cilium/values.yaml
	@echo "Waiting for Cilium to be ready..."
	kubectl -n kube-system rollout status ds/cilium
	kubectl apply -f payload/platform/cilium/lb-pools.yaml

install-cert-manager:
	$(call require,CERT_MANAGER_VERSION,payload/platform/cert-manager/application.yaml)
	helm repo add jetstack https://charts.jetstack.io
	helm repo update
	helm upgrade --install cert-manager jetstack/cert-manager \
		--namespace cert-manager \
		--create-namespace \
		--version $(CERT_MANAGER_VERSION) \
		--set crds.enabled=true \
		--set config.apiVersion=controller.config.cert-manager.io/v1alpha1 \
		--set config.kind=ControllerConfiguration \
		--set config.enableGatewayAPI=true \
		--set prometheus.enabled=true
	@echo "Waiting for Cert-Manager..."
	kubectl -n cert-manager rollout status deploy/cert-manager
	kubectl -n cert-manager rollout status deploy/cert-manager-webhook
	kubectl apply -f payload/platform/cert-manager/cluster-issuers.yaml

install-argo:
	$(call require,ARGOCD_VERSION,payload/argocd/application.yaml)
	helm repo add argocd https://argoproj.github.io/argo-helm
	helm repo update
	helm upgrade --install argocd argocd/argo-cd \
		--namespace argocd \
		--create-namespace \
		--values payload/argocd/values.yaml \
		--version $(ARGOCD_VERSION) \
		--wait

bootstrap-apps:
	@echo "Bootstrapping ArgoCD App-of-Apps..."
	kubectl apply -f payload/root.yaml
	@echo "Root app and core-infrastructure apps created."
	@echo "ArgoCD will now sync all applications from the Git repo."

clean:
	rm -rf output/*
