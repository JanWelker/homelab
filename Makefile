.PHONY: download config serve clean kubeconfig untaint install-argo 

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

install-core: install-cilium install-cert-manager 

install-cilium:
	-kubectl -n kube-system delete ds kube-proxy 2>/dev/null || true
	helm repo add cilium https://helm.cilium.io/
	helm repo update
	helm upgrade --install cilium cilium/cilium \
		--version 1.14.9 \
		--namespace kube-system \
		--values payload/core/cilium-values.yaml
	@echo "Waiting for Cilium to be ready..."
	kubectl -n kube-system rollout status ds/cilium
	kubectl apply -f payload/core/cilium-pool.yaml

install-cert-manager:
	kubectl apply -f payload/core/cert-manager.yaml
	@echo "Waiting for Cert-Manager..."
	kubectl -n cert-manager rollout status deploy/cert-manager
	kubectl -n cert-manager rollout status deploy/cert-manager-webhook

install-argo:
	helm repo add argocd https://argoproj.github.io/argo-helm
	helm repo update
	helm upgrade --install argocd argocd/argo-cd \
		--namespace argocd \
		--create-namespace \
		--values payload/bootstrap/argocd-values.yaml \
		--version 7.7.0 \
		--wait

clean:
	rm -rf output/*
