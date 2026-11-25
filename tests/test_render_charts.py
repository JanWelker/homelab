"""Tests for the parts of scripts/render-charts.py that emulate Argo CD offline."""

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'render-charts.py'
spec = importlib.util.spec_from_file_location('render_charts', SCRIPT)
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


def test_drop_nulls_keeps_everything_but_null_values():
    values = {'pdb': {'minAvailable': None, 'maxUnavailable': 1}, 'list': [{'a': None}, 2]}
    assert render.drop_nulls(values) == {'pdb': {'maxUnavailable': 1}, 'list': [{}, 2]}


@pytest.mark.parametrize(
    ('name', 'pattern', 'matched'),
    [
        ('application.yaml', '{application.yaml,values.yaml}', True),
        ('values.yaml', '{application.yaml,values.yaml}', True),
        ('gateway.yaml', '{application.yaml,values.yaml}', False),
        ('application.yaml', 'application.yaml', True),
        ('network-policies/argocd.yaml', 'application.yaml', False),
    ],
)
def test_brace_match(name, pattern, matched):
    assert render.brace_match(name, pattern) is matched


def test_duplicate_key_fails_the_render():
    text = 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: a\n  name: b\n'
    with pytest.raises(render.RenderError, match='duplicate key'):
        render.load_all(text, 'test')


def test_equals_sign_loads_as_a_string():
    text = 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: a\ndata:\n  op: =\n'
    assert render.load_all(text, 'test')[0]['data']['op'] == '='


def test_directory_file_without_objects_is_skipped():
    assert render.load_all('image:\n  tag: v1\n', 'values.yaml', lenient=True) == []


def test_mixed_file_fails_even_in_a_directory():
    text = 'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: a\n---\nfoo: bar\n'
    with pytest.raises(render.RenderError):
        render.load_all(text, 'mixed.yaml', lenient=True)


def test_list_is_unpacked():
    text = 'apiVersion: v1\nkind: List\nitems:\n- apiVersion: v1\n  kind: ConfigMap\n'
    assert [o['kind'] for o in render.load_all(text, 'test')] == ['ConfigMap']


@pytest.mark.parametrize(
    ('hook', 'skipped'),
    [('test', True), ('test-success', True), ('pre-install,pre-upgrade', False), ('', False)],
)
def test_only_test_hooks_are_left_out(hook, skipped):
    obj = {'kind': 'Pod', 'metadata': {'annotations': {'helm.sh/hook': hook} if hook else {}}}
    assert render.is_test_hook(obj) is skipped


def test_namespaced_objects_get_the_destination_namespace():
    app = {'spec': {'destination': {'namespace': 'logging'}}}
    objects = [
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'a'}},
        {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'b', 'namespace': 'x'}},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole', 'metadata': {}},
        {'apiVersion': 'example.com/v1', 'kind': 'Global', 'metadata': {}},
    ]
    scoped = render.CLUSTER_SCOPED | {('example.com', 'Global')}
    render.set_namespaces(app, objects, scoped)
    assert [o['metadata'].get('namespace') for o in objects] == ['logging', 'x', None, None]


def test_served_apis_come_from_crds_and_apiservices():
    crd = {
        'kind': 'CustomResourceDefinition',
        'spec': {
            'group': 'example.com',
            'scope': 'Cluster',
            'names': {'kind': 'Thing'},
            'versions': [{'name': 'v1', 'served': True}, {'name': 'v0', 'served': False}],
        },
    }
    service = {'kind': 'APIService', 'spec': {'group': 'metrics.k8s.io', 'version': 'v1beta1'}}
    apis, cluster = render.served_apis({'a': [crd, service]})
    assert apis == ['example.com/v1', 'example.com/v1/Thing', 'metrics.k8s.io/v1beta1']
    assert cluster == {('example.com', 'Thing')}


def test_value_files_resolve_through_the_ref_source(tmp_path):
    (tmp_path / 'values.yaml').write_text('a: 1\n')
    helm = {'valueFiles': ['$values/values.yaml']}
    assert render.value_files(helm, {'values': str(tmp_path)}) == [str(tmp_path / 'values.yaml')]
    with pytest.raises(render.RenderError, match='does not exist'):
        render.value_files({'valueFiles': ['$values/missing.yaml']}, {'values': str(tmp_path)})
    assert (
        render.value_files(
            {'valueFiles': ['$values/missing.yaml'], 'ignoreMissingValueFiles': True},
            {'values': str(tmp_path)},
        )
        == []
    )


def test_default_globs_are_the_applicationset_generator_paths():
    globs = render.default_globs()
    assert 'payload/platform/*/application.yaml' in globs
    assert 'payload/argocd/application.yaml' in globs
    assert all(render.ROOT.glob(g) for g in globs)


def test_generated_application_copies_the_file():
    app = {
        'metadata': {'name': 'x', 'namespace': 'elsewhere', 'finalizers': ['f']},
        'spec': {'project': 'infra'},
    }
    generated = render.generated_application(app)
    assert generated['metadata']['namespace'] == 'argocd'
    assert generated['metadata']['finalizers'] == ['f']
    assert generated['spec'] == {'project': 'infra'}
