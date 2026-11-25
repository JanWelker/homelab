#!/usr/bin/env python3
"""Render every platform Application the way Argo CD will.

    render-charts.py [--out <dir>] [<glob> ...]

The globs select `application.yaml` files relative to this repository's root
and default to the file paths of the `platform` ApplicationSet's git
generator, so the set is the one Argo CD generates. Every source of an
Application is rendered: Helm charts (`valuesObject`, `values`,
`valueFiles` with `$<ref>` paths, `parameters`), plain `directory` sources
(`recurse`, `include`, `exclude`) and `kustomize` sources (`namespace`,
`images`, `patches` and the other overlay fields Argo CD writes into a
kustomization). A source that points at this repository is read from the
working tree; any other git repository is cloned at its `targetRevision`.

With --out, each Application's objects are written to
`<dir>/<application>.yaml`, named after the Application, not its directory,
plus `<dir>/platform.yaml` holding the Application objects the ApplicationSet
generates from the selected files, and `<dir>/<application>.operator-crds.yaml`
for an Application whose operator registers its CRDs at runtime instead of
shipping them in the chart (Cilium, Tetragon): those are read from the
upstream repository at the chart's version, because the cluster serves them
and nothing that uses them validates without. Without --out only the exit
code and one line per Application remain. Requires helm and git on PATH, and kubectl for
kustomize sources.

What is emulated, because a plain `helm template` differs from Argo CD:

- `null` under `valuesObject` is dropped before Helm sees it. Argo CD stores
  the field as JSON and loses null keys, so a null cannot unset a chart
  default there; a `parameters` entry with the value `null` can.
- `--include-crds`, the destination namespace and the release name, and
  `--kube-version` from `kubernetes_version` in `ansible/inventory.yaml`.
- `--api-versions` for every CRD and APIService the rendered set serves, so a
  chart that checks `.Capabilities.APIVersions` renders as on the cluster.
- A namespaced object without a namespace gets the destination namespace;
  scope comes from the built-in kinds and the rendered CRDs.
- A Helm test hook is left out: Argo CD maps it to no sync phase and never
  applies it. Other hooks stay; Argo CD applies them as sync hooks.
- A file in a directory source holding no Kubernetes object, such as a
  `values.yaml` beside the manifests, is skipped.

A duplicate mapping key in rendered output fails the Application: the API
server rejects it, while a YAML library would silently keep the last value.

The interface is stable: the workloads repository's CI runs this script on a
checkout of `main` to get the platform's CRDs and admission policies.
"""

import argparse
import copy
import fnmatch
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
APPLICATIONSET = ROOT / 'payload/argocd/applicationset-platform.yaml'
INVENTORY = ROOT / 'ansible/inventory.yaml'
# The repository the Applications name for their own manifests.
SELF_REPOS = {'github.com/janwelker/homelab'}
ACTIONS = 'GITHUB_ACTIONS' in os.environ

# Built-in cluster-scoped kinds, as (group, kind); CRDs add theirs.
CLUSTER_SCOPED = {
    ('', 'Namespace'),
    ('', 'Node'),
    ('', 'PersistentVolume'),
    ('admissionregistration.k8s.io', 'MutatingAdmissionPolicy'),
    ('admissionregistration.k8s.io', 'MutatingAdmissionPolicyBinding'),
    ('admissionregistration.k8s.io', 'MutatingWebhookConfiguration'),
    ('admissionregistration.k8s.io', 'ValidatingAdmissionPolicy'),
    ('admissionregistration.k8s.io', 'ValidatingAdmissionPolicyBinding'),
    ('admissionregistration.k8s.io', 'ValidatingWebhookConfiguration'),
    ('apiextensions.k8s.io', 'CustomResourceDefinition'),
    ('apiregistration.k8s.io', 'APIService'),
    ('certificates.k8s.io', 'CertificateSigningRequest'),
    ('certificates.k8s.io', 'ClusterTrustBundle'),
    ('flowcontrol.apiserver.k8s.io', 'FlowSchema'),
    ('flowcontrol.apiserver.k8s.io', 'PriorityLevelConfiguration'),
    ('networking.k8s.io', 'IngressClass'),
    ('networking.k8s.io', 'IPAddress'),
    ('networking.k8s.io', 'ServiceCIDR'),
    ('node.k8s.io', 'RuntimeClass'),
    ('rbac.authorization.k8s.io', 'ClusterRole'),
    ('rbac.authorization.k8s.io', 'ClusterRoleBinding'),
    ('resource.k8s.io', 'DeviceClass'),
    ('scheduling.k8s.io', 'PriorityClass'),
    ('storage.k8s.io', 'CSIDriver'),
    ('storage.k8s.io', 'CSINode'),
    ('storage.k8s.io', 'StorageClass'),
    ('storage.k8s.io', 'VolumeAttachment'),
    ('storage.k8s.io', 'VolumeAttributesClass'),
}

HELM_FIELDS = {
    'releaseName',
    'valuesObject',
    'values',
    'valueFiles',
    'parameters',
    'skipCrds',
    'skipTests',
    'ignoreMissingValueFiles',
}
KUSTOMIZE_FIELDS = {
    'namespace',
    'namePrefix',
    'nameSuffix',
    'images',
    'patches',
    'commonLabels',
    'commonAnnotations',
}
DIRECTORY_FIELDS = {'recurse', 'include', 'exclude'}
# Charts whose operator registers its CRDs at runtime instead of shipping
# them: (repository, path) of the CRDs at the tag v<chart version>.
OPERATOR_CRDS = {
    'cilium': ('https://github.com/cilium/cilium.git', 'pkg/k8s/apis/cilium.io/client/crds'),
    'tetragon': ('https://github.com/cilium/tetragon.git', 'pkg/k8s/apis/cilium.io/client/crds'),
}
TEST_HOOKS = {'test', 'test-success', 'test-failure'}


class RenderError(Exception):
    """A source that cannot be rendered; the message says why."""


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses a duplicate key, as the API server does."""


def _strict_mapping(loader, node):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key != '<<' and key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f'duplicate key {key!r}', key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)
# A bare `=` resolves to YAML 1.1's value tag, which SafeLoader cannot build.
StrictLoader.add_constructor('tag:yaml.org,2002:value', yaml.SafeLoader.construct_yaml_str)


def is_object(doc):
    return isinstance(doc, dict) and 'apiVersion' in doc and 'kind' in doc


def load_all(text, origin, lenient=False):
    """The objects of a YAML stream; a List is unpacked as kubectl does.

    lenient is a directory source's file: Argo CD skips a file holding no
    Kubernetes object at all, such as a chart's values.yaml next to the
    manifests, but fails one that mixes objects with anything else.
    """
    try:
        docs = [doc for doc in yaml.load_all(text, Loader=StrictLoader) if doc]
    except yaml.YAMLError as error:
        raise RenderError(f'{origin}: {error}') from error
    if lenient and not any(is_object(doc) for doc in docs):
        return []
    objects = []
    for doc in docs:
        if not is_object(doc):
            raise RenderError(f'{origin}: a document that is not a Kubernetes object')
        if doc['kind'].endswith('List') and 'items' in doc:
            objects.extend(item for item in doc['items'] or [] if item)
        else:
            objects.append(doc)
    return objects


def drop_nulls(value):
    """valuesObject as Argo CD hands it to Helm: without null mapping values."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


def brace_match(name, pattern):
    """Go glob match as Argo CD's directory include/exclude uses it, braces included."""
    group = re.search(r'\{([^}]*)\}', pattern)
    if not group:
        return fnmatch.fnmatchcase(name, pattern)
    return any(
        brace_match(name, pattern[: group.start()] + alt + pattern[group.end() :])
        for alt in group.group(1).split(',')
    )


def kube_version():
    match = re.search(r'kubernetes_version:\s*["\']?(v[0-9.]+)', INVENTORY.read_text())
    if not match:
        raise RenderError(f'no kubernetes_version in {INVENTORY}')
    return match.group(1)


def default_globs():
    """The file paths the platform ApplicationSet's git generator reads."""
    appset = yaml.safe_load(APPLICATIONSET.read_text())
    return [
        entry['path']
        for generator in appset['spec']['generators']
        for entry in generator.get('git', {}).get('files', [])
    ]


def repo_key(url):
    return re.sub(r'(\.git)?/?$', '', re.sub(r'^https?://', '', url)).lower()


def is_helm(source):
    return 'chart' in source


def sources_of(app):
    return app['spec'].get('sources') or [app['spec']['source']]


def value_files(helm, refs):
    """The `$<ref>/...` value files of a chart source, as local paths."""
    paths = []
    for ref in helm.get('valueFiles', []):
        path = ref
        for name, root in refs.items():
            path = path.replace(f'${name}/', f'{root}/')
        if not pathlib.Path(path).is_absolute():
            raise RenderError(f'value file {ref} is not in a ref source; not supported')
        if pathlib.Path(path).exists():
            paths.append(path)
        elif not helm.get('ignoreMissingValueFiles'):
            raise RenderError(f'value file {ref} does not exist')
    return paths


class Renderer:
    """Renders Applications; holds the chart and clone caches for one run."""

    def __init__(self, workdir):
        self.workdir = pathlib.Path(workdir)
        self.charts = {}
        self.clones = {}
        self.kube_version = kube_version()
        self.api_versions = []

    def run(self, args, cwd=None):
        result = subprocess.run(
            args, cwd=cwd or self.workdir, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RenderError(f'{" ".join(args[:3])} failed: {result.stderr.strip()}')
        return result.stdout

    def checkout(self, source, sparse=None):
        """The local directory holding a git source's repository."""
        if repo_key(source['repoURL']) in SELF_REPOS:
            return ROOT
        key = (repo_key(source['repoURL']), str(source.get('targetRevision', 'HEAD')), sparse)
        if key not in self.clones:
            target = self.workdir / f'git-{len(self.clones)}'
            args = ['git', 'clone', '--quiet', '--depth', '1']
            if key[1] != 'HEAD':
                args += ['--branch', key[1]]
            if sparse:
                args += ['--filter=blob:none', '--sparse']
            self.run([*args, source['repoURL'], str(target)])
            if sparse:
                self.run(['git', '-C', str(target), 'sparse-checkout', 'set', sparse])
            self.clones[key] = target
        return self.clones[key]

    def operator_crds(self, app):
        """The CRDs an Application's operator registers itself, at the chart's version."""
        objects = []
        for source in sources_of(app):
            if source.get('chart') not in OPERATOR_CRDS:
                continue
            repo, path = OPERATOR_CRDS[source['chart']]
            tag = f'v{str(source["targetRevision"]).removeprefix("v")}'
            clone = self.checkout({'repoURL': repo, 'targetRevision': tag}, sparse=path)
            for file in sorted((clone / path).glob('**/*.yaml')):
                objects += load_all(file.read_text(), f'{repo} {tag} {file.name}')
        return objects

    def chart(self, source):
        """Path of the chart archive, pulled once per run."""
        key = (source['repoURL'], source['chart'], str(source['targetRevision']))
        if key not in self.charts:
            target = self.workdir / f'chart-{len(self.charts)}'
            target.mkdir()
            version = ['--version', str(source['targetRevision'])]
            if source['repoURL'].startswith(('http://', 'https://')):
                ref = [source['chart'], '--repo', source['repoURL'], *version]
            else:
                # Argo CD takes an OCI registry path without a scheme.
                ref = [f'oci://{source["repoURL"].rstrip("/")}/{source["chart"]}', *version]
            # From an empty directory: helm prefers a local directory named
            # like the chart over the repository.
            self.run(['helm', 'pull', *ref, '--destination', str(target)], cwd=target)
            self.charts[key] = next(target.glob('*.tgz'))
        return self.charts[key]

    def render_helm(self, app, source, refs):
        helm = source.get('helm', {})
        unknown = set(helm) - HELM_FIELDS
        if unknown:
            raise RenderError(f'unsupported helm fields {sorted(unknown)}')
        namespace = app['spec']['destination'].get('namespace', '')
        args = [
            'helm',
            'template',
            helm.get('releaseName', app['metadata']['name']),
            str(self.chart(source)),
            '--namespace',
            namespace or 'default',
            '--kube-version',
            self.kube_version,
        ]
        if not helm.get('skipCrds'):
            args.append('--include-crds')
        if helm.get('skipTests'):
            args.append('--skip-tests')
        for api in self.api_versions:
            args += ['--api-versions', api]
        with tempfile.TemporaryDirectory(dir=self.workdir) as tmp:
            for path in value_files(helm, refs):
                args += ['--values', path]
            # valuesObject wins over values when both are set.
            if 'valuesObject' in helm:
                inline = yaml.safe_dump(drop_nulls(helm['valuesObject']))
            else:
                inline = helm.get('values', '')
            if inline:
                values = pathlib.Path(tmp, 'values.yaml')
                values.write_text(inline)
                args += ['--values', str(values)]
            for parameter in helm.get('parameters', []):
                flag = '--set-string' if parameter.get('forceString') else '--set'
                args += [flag, f'{parameter["name"]}={parameter["value"]}']
            return self.run(args, cwd=tmp)

    def render_directory(self, source, base):
        directory = source.get('directory', {})
        unknown = set(directory) - DIRECTORY_FIELDS
        if unknown:
            raise RenderError(f'unsupported directory fields {sorted(unknown)}')
        pattern = '**/*' if directory.get('recurse') else '*'
        texts = []
        for file in sorted(base.glob(pattern)):
            if file.suffix not in {'.yaml', '.yml', '.json'} or not file.is_file():
                continue
            relative = file.relative_to(base).as_posix()
            if directory.get('include') and not brace_match(relative, directory['include']):
                continue
            if directory.get('exclude') and brace_match(relative, directory['exclude']):
                continue
            texts.append((relative, file.read_text()))
        return texts

    def render_kustomize(self, source, base):
        options = source.get('kustomize', {})
        unknown = set(options) - KUSTOMIZE_FIELDS
        if unknown:
            raise RenderError(f'unsupported kustomize fields {sorted(unknown)}')
        overlay = pathlib.Path(tempfile.mkdtemp(dir=self.workdir))
        kustomization = {
            'apiVersion': 'kustomize.config.k8s.io/v1beta1',
            'kind': 'Kustomization',
            'resources': [os.path.relpath(base, overlay)],
        }
        for field in ('namespace', 'namePrefix', 'nameSuffix', 'patches'):
            if field in options:
                kustomization[field] = copy.deepcopy(options[field])
        if 'commonLabels' in options:
            kustomization['labels'] = [{'pairs': options['commonLabels']}]
        if 'commonAnnotations' in options:
            kustomization['commonAnnotations'] = options['commonAnnotations']
        images = []
        for image in options.get('images', []):
            # name=newName:newTag, or name:newTag, as `kustomize edit set image` takes it.
            name, _, new = image.partition('=')
            new = new or name
            entry = {'name': re.sub(r'[:@].*$', '', name)}
            ref, _, digest = new.partition('@')
            new_name, _, tag = ref.rpartition(':') if ':' in ref.split('/')[-1] else (ref, '', '')
            if new_name != entry['name']:
                entry['newName'] = new_name
            if tag:
                entry['newTag'] = tag
            if digest:
                entry['digest'] = digest
            images.append(entry)
        if images:
            kustomization['images'] = images
        (overlay / 'kustomization.yaml').write_text(yaml.safe_dump(kustomization))
        return self.run(['kubectl', 'kustomize', str(overlay)])

    def render(self, app):
        """Every object one Application deploys, in source order."""
        sources = sources_of(app)
        refs = {s['ref']: str(self.checkout(s)) for s in sources if 'ref' in s}
        objects = []
        for index, source in enumerate(sources):
            origin = f'source {index + 1}'
            if is_helm(source):
                text = self.render_helm(app, source, refs)
                objects += [
                    obj
                    for obj in load_all(text, f'{origin} ({source["chart"]})')
                    if not is_test_hook(obj)
                ]
                continue
            if 'path' not in source:
                continue  # a ref-only source holds value files
            base = self.checkout(source) / source['path']
            if not base.is_dir():
                raise RenderError(f'{origin}: {source["path"]} is not a directory')
            if 'kustomize' in source or (
                'directory' not in source
                and any((base / n).exists() for n in ('kustomization.yaml', 'kustomization.yml'))
            ):
                objects += load_all(self.render_kustomize(source, base), f'{origin} (kustomize)')
            elif 'directory' in source or not (base / 'Chart.yaml').exists():
                for relative, text in self.render_directory(source, base):
                    objects += load_all(text, f'{origin} ({relative})', lenient=True)
            else:
                raise RenderError(f'{origin}: a local Helm chart is not supported')
        return objects


def served_apis(rendered):
    """--api-versions entries and cluster-scoped kinds from the rendered CRDs and APIServices."""
    apis, cluster = set(), set()
    for objects in rendered.values():
        for obj in objects:
            if obj['kind'] == 'CustomResourceDefinition':
                spec = obj['spec']
                for version in spec.get('versions', []):
                    if version.get('served', True):
                        apis.add(f'{spec["group"]}/{version["name"]}')
                        apis.add(f'{spec["group"]}/{version["name"]}/{spec["names"]["kind"]}')
                if spec.get('scope') == 'Cluster':
                    cluster.add((spec['group'], spec['names']['kind']))
            elif obj['kind'] == 'APIService':
                apis.add(f'{obj["spec"]["group"]}/{obj["spec"]["version"]}')
    return sorted(apis), cluster


def is_test_hook(obj):
    """A Helm test hook, which Argo CD maps to no sync phase and so never applies."""
    hooks = ((obj.get('metadata') or {}).get('annotations') or {}).get('helm.sh/hook', '')
    return bool(hooks) and set(re.split(r'\s*,\s*', hooks.strip())) <= TEST_HOOKS


def group_of(obj):
    api = obj.get('apiVersion', '')
    return api.rpartition('/')[0] if '/' in api else ''


def set_namespaces(app, objects, cluster_scoped):
    """Argo CD puts a namespaced object without a namespace into the destination's."""
    namespace = app['spec']['destination'].get('namespace')
    for obj in objects:
        metadata = obj.setdefault('metadata', {})
        if (group_of(obj), obj['kind']) in cluster_scoped:
            continue
        if namespace and not metadata.get('namespace'):
            metadata['namespace'] = namespace


def generated_application(app):
    """The Application the platform ApplicationSet makes of one file."""
    metadata = app['metadata']
    return {
        'apiVersion': 'argoproj.io/v1alpha1',
        'kind': 'Application',
        'metadata': {
            'name': metadata['name'],
            'namespace': 'argocd',
            'labels': metadata.get('labels') or {},
            'annotations': metadata.get('annotations') or {},
            'finalizers': metadata.get('finalizers') or [],
        },
        'spec': app['spec'],
    }


def applications(globs):
    """(path, Application) for every selected file, in path order."""
    found = []
    for pattern in globs:
        for path in sorted(ROOT.glob(pattern)):
            for doc in yaml.safe_load_all(path.read_text()):
                if doc and doc.get('kind') == 'Application':
                    found.append((path.relative_to(ROOT), doc))
    return found


def report(name, message):
    prefix = f'::error title={name}::' if ACTIONS else 'error: '
    print(f'{prefix}{name}: {message}', file=sys.stderr)


def render_all(globs, workdir):
    """Render the selected Applications.

    Returns the (path, Application) pairs, {name: objects}, {name: operator
    CRDs} and {name: failure}. Charts render twice: the second pass hands Helm
    the APIs the first pass found, as Argo CD hands it the cluster's.
    """
    renderer = Renderer(workdir)
    apps = applications(globs)
    rendered, operator, failures = {}, {}, {}
    for attempt in range(2):
        for _, app in apps:
            name = app['metadata']['name']
            if attempt == 1 and not any(is_helm(s) for s in sources_of(app)):
                continue
            try:
                rendered[name] = renderer.render(app)
                if attempt == 0:
                    operator[name] = renderer.operator_crds(app)
                failures.pop(name, None)
            except RenderError as error:
                failures[name] = str(error)
        renderer.api_versions, cluster = served_apis({**rendered, **operator})
    for _, app in apps:
        if app['metadata']['name'] in rendered:
            set_namespaces(app, rendered[app['metadata']['name']], CLUSTER_SCOPED | cluster)
    return apps, rendered, {k: v for k, v in operator.items() if v}, failures


def dump(path, objects):
    path.write_text(yaml.safe_dump_all(objects, sort_keys=False))


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=pathlib.Path)
    parser.add_argument('globs', nargs='*')
    args = parser.parse_args(argv[1:])
    globs = args.globs or default_globs()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as workdir:
        apps, rendered, operator, failures = render_all(globs, workdir)

    for path, app in apps:
        name = app['metadata']['name']
        if name in failures:
            report(name, f'{path}: {failures[name]}')
            continue
        extra = f', {len(operator[name])} operator CRDs' if name in operator else ''
        print(f'{name}: {len(rendered[name])} objects{extra} from {path}')
        if args.out:
            dump(args.out / f'{name}.yaml', rendered[name])
            if name in operator:
                dump(args.out / f'{name}.operator-crds.yaml', operator[name])
    if args.out:
        dump(args.out / 'platform.yaml', [generated_application(app) for _, app in apps])
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
