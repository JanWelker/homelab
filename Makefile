.PHONY: download config serve clean kubeconfig untaint taint install-argo bootstrap-apps

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt
	@echo "Virtual environment created. Activate with: source .venv/bin/activate"

artifacts: download config

download:
	ansible-playbook -i ansible/inventory.yml ansible/playbooks/download.yml

config:
	ansible-playbook -i ansible/inventory.yml ansible/playbooks/config.yml

serve:
	sudo python3 boot_server/serve.py

kubeconfig:
	ansible-playbook -i ansible/inventory.yml ansible/playbooks/kubeconfig.yml
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
		--values payload/core/cilium/cilium-values.yaml
	@echo "Waiting for Cilium to be ready..."
	kubectl -n kube-system rollout status ds/cilium
	kubectl apply -f payload/core/cilium-pool.yaml

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
	kubectl apply -f payload/core/cluster-issuer-prod.yaml

install-argo:
	helm repo add argocd https://argoproj.github.io/argo-helm
	helm repo update
	helm upgrade --install argocd argocd/argo-cd \
		--namespace argocd \
		--create-namespace \
		--values payload/bootstrap/argocd-values.yaml \
		--version 7.7.0 \
		--wait

bootstrap-apps:
	@echo "Bootstrapping ArgoCD App-of-Apps..."
	kubectl apply -f payload/root-app.yaml
	@echo "Root app and core-infrastructure apps created."
	@echo "ArgoCD will now sync all applications from the Git repo."

clean:
	rm -rf output/*
