.PHONY: download config serve clean kubeconfig untaint taint install-argo bootstrap-apps

setup:
	uv sync
	@echo "Virtual environment created. Activate with: source .venv/bin/activate"

artifacts: download config

download:
	uv run ansible-playbook -i ansible/inventory.yml ansible/playbooks/download.yml

config:
	uv run ansible-playbook -i ansible/inventory.yml ansible/playbooks/config.yml

serve:
	sudo python3 boot_server/serve.py

kubeconfig:
	uv run ansible-playbook -i ansible/inventory.yml ansible/playbooks/kubeconfig.yml
	@echo "Kubeconfig saved to output/kubeconfig"

untaint:
	@echo "WARNING: Only run this task in a single node cluster setup!"
	kubectl taint nodes --all node-role.kubernetes.io/control-plane-

taint:
	@echo "Re-applying control-plane taints..."
	kubectl taint nodes -l node-role.kubernetes.io/control-plane node-role.kubernetes.io/control-plane:NoSchedule

install-core: install-cilium install-cert-manager 

install-cilium:
	-kubectl -n kube-system delete ds kube-proxy 2>/dev/null || true
	@echo "Installing Gateway API CRDs..."
	kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.0/config/crd/standard/gateway.networking.k8s.io_gatewayclasses.yaml
	kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.0/config/crd/standard/gateway.networking.k8s.io_gateways.yaml
	kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.0/config/crd/standard/gateway.networking.k8s.io_httproutes.yaml
	kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.0/config/crd/standard/gateway.networking.k8s.io_referencegrants.yaml
	kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.0/config/crd/standard/gateway.networking.k8s.io_grpcroutes.yaml
	kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.2.0/config/crd/experimental/gateway.networking.k8s.io_tlsroutes.yaml
	helm repo add cilium https://helm.cilium.io/
	helm repo update
	helm upgrade --install cilium cilium/cilium \
		--version 1.18.5 \
		--namespace kube-system \
		--values payload/platform/cilium/cilium-values.yaml
	@echo "Waiting for Cilium to be ready..."
	kubectl -n kube-system rollout status ds/cilium
	kubectl apply -f payload/platform/cilium-pool.yaml

install-cert-manager:
	helm repo add jetstack https://charts.jetstack.io
	helm repo update
	helm upgrade --install cert-manager jetstack/cert-manager \
		--namespace cert-manager \
		--create-namespace \
		--version v1.16.2 \
		--set crds.enabled=true \
		--set config.apiVersion=controller.config.cert-manager.io/v1alpha1 \
		--set config.kind=ControllerConfiguration \
		--set config.enableGatewayAPI=true \
		--set prometheus.enabled=true
	@echo "Waiting for Cert-Manager..."
	kubectl -n cert-manager rollout status deploy/cert-manager
	kubectl -n cert-manager rollout status deploy/cert-manager-webhook
	kubectl apply -f payload/platform/cluster-issuer-prod.yaml

install-infisical:
	@echo "Setting up Infisical Secrets..."
	kubectl create namespace infisical --dry-run=client -o yaml | kubectl apply -f -
	@if ! kubectl -n infisical get secret infisical-secrets >/dev/null 2>&1; then \
		echo "Generating initial encryption keys..."; \
		ENC_KEY=$$(openssl rand -hex 16); \
		AUTH_SEC=$$(openssl rand -base64 32); \
		kubectl -n infisical create secret generic infisical-secrets \
		--from-literal=ENCRYPTION_KEY=$$ENC_KEY \
		--from-literal=AUTH_SECRET=$$AUTH_SEC; \
		echo "Secret 'infisical-secrets' created."; \
	else \
		echo "Secret 'infisical-secrets' already exists. Skipping."; \
	fi

install-argo:
	helm repo add argocd https://argoproj.github.io/argo-helm
	helm repo update
	helm upgrade --install argocd argocd/argo-cd \
		--namespace argocd \
		--create-namespace \
		--values payload/argocd/argocd-values.yaml \
		--version 9.1.9 \
		--wait

bootstrap-apps:
	@echo "Bootstrapping ArgoCD App-of-Apps..."
	kubectl apply -f payload/root.yaml
	@echo "Root app and core-infrastructure apps created."
	@echo "ArgoCD will now sync all applications from the Git repo."

clean:
	rm -rf output/*
