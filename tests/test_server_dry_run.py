"""Tests for the offline half of scripts/server-dry-run.py: what goes where, and how."""

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'server-dry-run.py'
spec = importlib.util.spec_from_file_location('server_dry_run', SCRIPT)
dry_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dry_run)

CRD = {
    'apiVersion': 'apiextensions.k8s.io/v1',
    'kind': 'CustomResourceDefinition',
    'metadata': {'name': 'things.example.com'},
    'spec': {'conversion': {'strategy': 'Webhook', 'webhook': {'clientConfig': {}}}},
}
POLICY = {
    'apiVersion': 'admissionregistration.k8s.io/v1',
    'kind': 'ValidatingAdmissionPolicy',
    'metadata': {'name': 'p'},
}
NAMESPACE = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'logging'}}
WEBHOOK = {
    'apiVersion': 'admissionregistration.k8s.io/v1',
    'kind': 'ValidatingWebhookConfiguration',
    'metadata': {'name': 'w'},
}
CONFIGMAP = {
    'apiVersion': 'v1',
    'kind': 'ConfigMap',
    'metadata': {'name': 'c', 'namespace': 'claude-docs'},
}


def test_partition_applies_only_crds_policies_and_namespaces():
    phases = dry_run.partition({'loki': [CRD, POLICY, NAMESPACE, WEBHOOK, CONFIGMAP]})
    kinds = {phase: [o['kind'] for o in apps.get('loki', [])] for phase, apps in phases.items()}
    assert kinds == {
        'crds': ['CustomResourceDefinition'],
        'policies': ['ValidatingAdmissionPolicy'],
        'namespaces': ['Namespace'],
        'dry_run': ['ValidatingWebhookConfiguration', 'ConfigMap'],
    }


def test_conversion_webhook_is_stripped_without_touching_the_input():
    crd = dry_run.partition({'a': [CRD]})['crds']['a'][0]
    assert crd['spec']['conversion'] == {'strategy': 'None'}
    assert CRD['spec']['conversion']['strategy'] == 'Webhook'


def test_tracking_id_is_argo_cds_annotation():
    obj = dry_run.partition({'loki': [CONFIGMAP]})['dry_run']['loki'][0]
    assert obj['metadata']['annotations'] == {
        'argocd.argoproj.io/tracking-id': 'loki:/ConfigMap:claude-docs/c'
    }
    assert 'annotations' not in CONFIGMAP['metadata']


def test_operator_crds_and_generated_applications_carry_no_tracking_id():
    phases = dry_run.partition({'cilium.operator-crds': [CRD], 'platform': [CONFIGMAP]})
    assert 'annotations' not in phases['crds']['cilium.operator-crds'][0]['metadata']
    assert 'annotations' not in phases['dry_run']['platform'][0]['metadata']
    assert dry_run.application_of('cilium.operator-crds') == 'cilium'


def test_unrendered_namespaces_are_those_no_application_renders():
    kube = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'namespace': 'kube-system'}}
    logged = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'namespace': 'logging'}}
    apps = {'a': [NAMESPACE, CONFIGMAP, kube, logged]}
    assert dry_run.unrendered_namespaces(apps) == ['claude-docs']


def test_refusals_drop_warnings():
    stderr = (
        'Warning: would violate PodSecurity "restricted:latest"\n'
        'W1125 08:45:22.187252 lease.go:270] something\n'
        'Error from server (Invalid): PodDisruptionBudget.policy "x" is invalid\n'
    )
    assert dry_run.refusals(stderr) == [
        'Error from server (Invalid): PodDisruptionBudget.policy "x" is invalid'
    ]


def test_canaries_are_valid_policies():
    kinds = [o['kind'] for o in dry_run.canaries()]
    assert set(kinds) == dry_run.POLICY_KINDS
    expression = dry_run.canaries()[2]['spec']['mutations'][0]['applyConfiguration']['expression']
    assert expression.count('{') == expression.count('}')
