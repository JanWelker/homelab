"""The repository's documented rules, checked against what Argo CD will apply.

The Application checks read the `application.yaml` files. The rest read the
output of `scripts/render-charts.py --out <dir>`, from $RENDERED_DIR or
output/rendered, and skip when neither exists; the Server Dry Run workflow
renders first and runs this file. Each assertion names the page that
documents its rule. An exception is an ALLOWED entry with its reason; an
entry nothing matches any more fails too, so the list cannot rot.
"""

import importlib.util
import json
import os
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = 'https://homelab.wlkr.ch'
APPS_DOCS = 'https://homelab-apps.wlkr.ch'
RENDERED = pathlib.Path(os.environ.get('RENDERED_DIR', ROOT / 'output' / 'rendered'))

spec = importlib.util.spec_from_file_location('render_charts', ROOT / 'scripts/render-charts.py')
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

WORKLOADS = {'Pod', 'Deployment', 'StatefulSet', 'DaemonSet', 'ReplicaSet', 'Job', 'CronJob'}

# Exceptions, by the identifier each check prints, with the reason.
ALLOWED = {
    'automount': {
        'backup: CronJob backup/openbao-snapshot as openbao-snapshot': (
            'no API access, but it logs in to OpenBao with the token through Kubernetes auth'
        ),
        'external-secrets: Deployment external-secrets/external-secrets-webhook'
        ' as external-secrets-webhook': 'needs the in-cluster config to start at all',
        'rook-ceph-cluster: Deployment rook-ceph/rook-ceph-tools as rook-ceph-default': (
            'the chart offers no switch for rook-ceph-default'
        ),
    },
    'cpu-limits': {
        'cilium: DaemonSet kube-system/cilium'
        ' spec.template.spec.initContainers[5].resources.limits.cpu': (
            'install-cni-binaries copies files and exits; unsetting it rolls every Cilium'
            ' agent, so it goes with the next Cilium bump'
        ),
    },
    'chart-network-policies': {
        'rook-ceph-operator: NetworkPolicy rook-ceph/ceph-csi-ceph-csi-controller-manager': (
            'the ceph-csi-operator subchart renders it unconditionally, no value turns it off'
        ),
    },
    'route-headers': {},
    'route-parents': {},
    'namespace-policies': {},
}


def documented(violations, rule, doc):
    """Assert violations are exactly the allowed ones, naming the doc."""
    allowed = ALLOWED[rule]
    unexpected = sorted(set(violations) - set(allowed))
    stale = sorted(set(allowed) - set(violations))
    assert not unexpected, f'{rule}, see {doc}:\n  ' + '\n  '.join(unexpected)
    assert not stale, f'{rule}: allowed but no longer found, drop from ALLOWED:\n  ' + '\n  '.join(
        stale
    )


@pytest.fixture(scope='module')
def rendered():
    """{application: objects} from the render, without the operator CRDs."""
    if not RENDERED.is_dir():
        pytest.skip(f'render first: uv run scripts/render-charts.py --out {RENDERED}')
    apps = {}
    for path in sorted(RENDERED.glob('*.yaml')):
        if path.stem == 'platform' or path.stem.endswith('.operator-crds'):
            continue
        apps[path.stem] = [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
    return apps


def objects(rendered, *kinds):
    for app, objs in rendered.items():
        for obj in objs:
            if not kinds or obj['kind'] in kinds:
                yield app, obj


def ref(app, obj):
    metadata = obj.get('metadata', {})
    namespace = metadata.get('namespace')
    name = f'{namespace}/{metadata.get("name")}' if namespace else metadata.get('name')
    return f'{app}: {obj["kind"]} {name}'


def pod_spec(obj):
    """The pod spec a workload object runs, or None."""
    if obj['kind'] == 'Pod':
        return obj.get('spec')
    if obj['kind'] == 'CronJob':
        return obj['spec']['jobTemplate']['spec']['template'].get('spec')
    return obj['spec']['template'].get('spec')


def containers(spec):
    return (spec.get('initContainers') or []) + (spec.get('containers') or [])


# --- Applications -----------------------------------------------------------


def test_every_application_has_the_same_sync_policy():
    found = {}
    for path, app in render.applications(render.default_globs()):
        policy = app['spec'].get('syncPolicy') or {}
        automated, retry = policy.get('automated'), policy.get('retry')
        expected_prune = app['metadata']['name'] != 'kube-vip'
        assert automated == {'prune': expected_prune, 'selfHeal': True}, (
            f'{path}: automated must be prune: {expected_prune}, selfHeal: true; '
            f'see {DOCS}/architecture/gitops/#sync-policy'
        )
        found.setdefault(json.dumps(retry, sort_keys=True), []).append(str(path))
    assert 'null' not in found, (
        f'no retry block in {found.get("null")}; see {DOCS}/architecture/gitops/#sync-policy'
    )
    assert len(found) == 1, (
        f'retry blocks differ, copy one from a sibling: {found}; '
        f'see {DOCS}/architecture/gitops/#sync-policy'
    )


# --- Rendered objects -------------------------------------------------------


def cpu_limits(value, path=''):
    """Every path in an object where a `limits` mapping names cpu."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'limits' and isinstance(child, dict) and 'cpu' in child:
                yield f'{path}.limits.cpu'
            yield from cpu_limits(child, f'{path}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from cpu_limits(child, f'{path}[{index}]')


def test_no_cpu_limits(rendered):
    violations = [
        f'{ref(app, obj)} {path}'
        for app, obj in objects(rendered)
        if obj['kind'] != 'CustomResourceDefinition'
        for path in cpu_limits(obj.get('spec', {}), 'spec')
    ]
    documented(violations, 'cpu-limits', f'{DOCS}/platform/#components')


def bound_service_accounts(rendered):
    """(namespace, name) of every ServiceAccount a binding names, and bound namespaces."""
    accounts, namespaces = set(), set()
    for _, binding in objects(rendered, 'RoleBinding', 'ClusterRoleBinding'):
        for subject in binding.get('subjects') or []:
            if subject.get('kind') == 'ServiceAccount':
                namespace = subject.get('namespace') or binding['metadata'].get('namespace')
                accounts.add((namespace, subject['name']))
            elif subject.get('kind') == 'Group' and subject['name'] in {
                'system:serviceaccounts',
                'system:authenticated',
            }:
                namespaces.add('*')
            elif subject.get('kind') == 'Group' and subject['name'].startswith(
                'system:serviceaccounts:'
            ):
                namespaces.add(subject['name'].removeprefix('system:serviceaccounts:'))
    return accounts, namespaces


def test_pods_without_api_access_mount_no_token(rendered):
    accounts = {
        (obj['metadata'].get('namespace'), obj['metadata']['name']): obj
        for _, obj in objects(rendered, 'ServiceAccount')
    }
    bound, bound_namespaces = bound_service_accounts(rendered)
    violations = []
    for app, obj in objects(rendered, *WORKLOADS):
        spec = pod_spec(obj) or {}
        namespace = obj['metadata'].get('namespace')
        name = spec.get('serviceAccountName') or 'default'
        mount = spec.get('automountServiceAccountToken')
        if mount is None:
            mount = (accounts.get((namespace, name)) or {}).get('automountServiceAccountToken')
        if mount is False:
            continue
        if (namespace, name) in bound or {namespace, '*'} & bound_namespaces:
            continue
        violations.append(f'{ref(app, obj)} as {name}')
    documented(
        violations, 'automount', f'{DOCS}/platform/security-policies/#default-serviceaccount-tokens'
    )


def https_listeners(rendered):
    """(gateway namespace, gateway, listener) for every HTTPS listener rendered here."""
    return {
        (gateway['metadata'].get('namespace'), gateway['metadata']['name'], listener['name'])
        for _, gateway in objects(rendered, 'Gateway')
        for listener in gateway['spec'].get('listeners', [])
        if listener.get('protocol') == 'HTTPS'
    }


def sets_hsts(rule):
    for route_filter in rule.get('filters') or []:
        modifier = route_filter.get('responseHeaderModifier') or {}
        headers = (modifier.get('set') or []) + (modifier.get('add') or [])
        if route_filter.get('type') == 'ResponseHeaderModifier' and any(
            h.get('name', '').lower() == 'strict-transport-security' for h in headers
        ):
            return True
    return False


def test_https_routes_carry_the_security_headers(rendered):
    listeners = https_listeners(rendered)
    violations = []
    for app, route in objects(rendered, 'HTTPRoute'):
        namespace = route['metadata'].get('namespace')
        on_https = any(
            (parent.get('namespace', namespace), parent['name'], parent.get('sectionName'))
            in listeners
            for parent in route['spec'].get('parentRefs') or []
        )
        if not on_https:
            continue
        violations += [
            f'{ref(app, route)} rule {index}'
            for index, rule in enumerate(route['spec'].get('rules') or [])
            if not sets_hsts(rule)
        ]
    documented(violations, 'route-headers', f'{DOCS}/platform/gateway-api/#usage')


def test_route_parents_are_spelled_out(rendered):
    violations = []
    for app, route in objects(rendered, 'HTTPRoute', 'GRPCRoute', 'TLSRoute'):
        for parent in route['spec'].get('parentRefs') or []:
            missing = [
                field
                for field, value in (
                    ('group', 'gateway.networking.k8s.io'),
                    ('kind', 'Gateway'),
                    ('sectionName', None),
                )
                if field not in parent or (value and parent[field] != value)
            ]
            if missing:
                violations.append(f'{ref(app, route)} parent {parent["name"]}: {missing}')
    documented(
        violations,
        'route-parents',
        f'{APPS_DOCS}/conventions/#writing-an-httproute and {DOCS}/platform/gateway-api/#pitfalls',
    )


def test_charts_ship_no_network_policies(rendered):
    documented(
        [ref(app, obj) for app, obj in objects(rendered, 'NetworkPolicy')],
        'chart-network-policies',
        f'{DOCS}/platform/security-policies/#what-every-policy-contains',
    )


def test_every_namespace_with_pods_has_its_deny_pair(rendered):
    policies = {
        (obj['metadata'].get('namespace'), obj['metadata']['name'])
        for _, obj in objects(rendered, 'CiliumNetworkPolicy')
    }
    namespaces = {
        obj['metadata'].get('namespace')
        for _, obj in objects(rendered, *WORKLOADS)
        if not (pod_spec(obj) or {}).get('hostNetwork')
    }
    violations = [
        f'{namespace}: {name}'
        for namespace in sorted(namespaces - {'kube-system'})
        for name in ('default-ingress', 'default-egress')
        if (namespace, name) not in policies
    ]
    documented(
        violations,
        'namespace-policies',
        f'{DOCS}/platform/security-policies/#network-policies',
    )


def test_network_policy_files_hold_their_own_namespace():
    directory = ROOT / 'payload/platform/security/network-policies'
    violations = []
    for path in sorted(directory.glob('*.yaml')):
        for obj in yaml.safe_load_all(path.read_text()):
            if (
                obj
                and obj['kind'] == 'CiliumNetworkPolicy'
                and obj['metadata'].get('namespace') != path.stem
            ):
                violations.append(f'{path.name}: {obj["metadata"]["name"]}')
    assert not violations, (
        f'one file per namespace, see {DOCS}/platform/security-policies/#network-policies: '
        f'{violations}'
    )
