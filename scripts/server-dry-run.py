#!/usr/bin/env python3
"""Send every rendered object to a real API server and report what it refuses.

    server-dry-run.py [--kubectl <command>] [--any-context] <rendered-dir>

<rendered-dir> is what `render-charts.py --out` writes. The API server is the
kubeconfig's current context, a throwaway kind cluster at the Kubernetes
minor the nodes run: this script applies objects to it for real, so it
refuses a context not named `kind-*` unless --any-context says otherwise.

1. Every CRD is applied, conversion webhook stripped, and must go Established.
2. The admission policies and their bindings are applied, and a canary of
   each kind waits until the API server enforces them.
3. Every Namespace is applied, then any namespace an object names that no
   Application renders (the workloads repository's, say) is created plainly.
4. Everything else gets `kubectl apply --server-side --dry-run=server`, one
   call per Application, and every refusal is reported under its Application.

Nothing that needs a controller is applied for real: a webhook configuration
or an APIService only goes through the dry run, so no request is ever sent to
a service that does not run. A CRD's conversion webhook is dropped for the
same reason; the schema of each served version still applies.

Objects go in as Argo CD sends them: server-side, under Argo CD's field
manager, with its tracking-id annotation, impersonating the application
controller's ServiceAccount. A policy that reads `request.userInfo` therefore
judges exactly the identity a sync has: the ones scoped to other identities
(the agents, the cert-manager and CloudNativePG controllers) do not match, as
they do not on the cluster, and the ones keyed on the tracking id do.
"""

import argparse
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time

import yaml

FIELD_MANAGER = 'argocd-controller'
CONTROLLER = 'system:serviceaccount:argocd:argocd-application-controller'
CONTROLLER_GROUPS = (
    'system:serviceaccounts',
    'system:serviceaccounts:argocd',
    'system:authenticated',
)
POLICY_KINDS = {
    'ValidatingAdmissionPolicy',
    'ValidatingAdmissionPolicyBinding',
    'MutatingAdmissionPolicy',
    'MutatingAdmissionPolicyBinding',
}
# Created by the API server itself, never by an Application.
BUILTIN_NAMESPACES = {'default', 'kube-system', 'kube-public', 'kube-node-lease'}
# Applications whose objects Argo CD does not apply, so they carry no tracking id.
UNTRACKED = {'platform'}
OPERATOR_SUFFIX = '.operator-crds'
CANARY = 'server-dry-run-canary'
ACTIONS = 'GITHUB_ACTIONS' in os.environ


def group_of(obj):
    api = obj.get('apiVersion', '')
    return api.rpartition('/')[0] if '/' in api else ''


def load(directory):
    """{application: objects} from a render-charts.py output directory.

    `<app>.operator-crds.yaml` stays a separate entry: the operator creates
    those CRDs, Argo CD does not apply them.
    """
    apps = {}
    for path in sorted(pathlib.Path(directory).glob('*.yaml')):
        apps[path.stem] = [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
    return apps


def application_of(entry):
    """The Application a rendered file belongs to."""
    return entry.removesuffix(OPERATOR_SUFFIX)


def tracked(entry):
    return entry not in UNTRACKED and not entry.endswith(OPERATOR_SUFFIX)


def tracking_id(app, obj):
    """Argo CD's annotation tracking id: <app>:<group>/<kind>:<namespace>/<name>."""
    metadata = obj.get('metadata', {})
    return (
        f'{app}:{group_of(obj)}/{obj["kind"]}:'
        f'{metadata.get("namespace", "")}/{metadata.get("name", "")}'
    )


def as_argocd(app, obj):
    """A copy of obj as Argo CD sends it, tracking annotation included."""
    obj = yaml.safe_load(yaml.safe_dump(obj))
    if tracked(app):
        annotations = obj.setdefault('metadata', {}).setdefault('annotations', {}) or {}
        annotations['argocd.argoproj.io/tracking-id'] = tracking_id(app, obj)
        obj['metadata']['annotations'] = annotations
    return obj


def without_conversion_webhook(crd):
    """A CRD whose conversion no longer calls a service that does not run here."""
    crd = yaml.safe_load(yaml.safe_dump(crd))
    if (crd['spec'].get('conversion') or {}).get('strategy') == 'Webhook':
        crd['spec']['conversion'] = {'strategy': 'None'}
    return crd


def partition(apps):
    """Split the rendered objects into the four phases, each {application: objects}."""
    phases = {'crds': {}, 'policies': {}, 'namespaces': {}, 'dry_run': {}}
    for entry, objects in apps.items():
        for obj in objects:
            kind = (group_of(obj), obj['kind'])
            if kind == ('apiextensions.k8s.io', 'CustomResourceDefinition'):
                phase, sent = 'crds', without_conversion_webhook(obj)
            elif kind[0] == 'admissionregistration.k8s.io' and kind[1] in POLICY_KINDS:
                phase, sent = 'policies', obj
            elif kind == ('', 'Namespace'):
                phase, sent = 'namespaces', obj
            else:
                phase, sent = 'dry_run', obj
            phases[phase].setdefault(entry, []).append(as_argocd(entry, sent))
    return phases


def unrendered_namespaces(apps):
    """Namespaces objects name that no Application renders and the API server lacks."""
    named = {
        obj['metadata']['namespace']
        for objects in apps.values()
        for obj in objects
        if (obj.get('metadata') or {}).get('namespace')
    }
    rendered = {
        obj['metadata']['name']
        for objects in apps.values()
        for obj in objects
        if obj['kind'] == 'Namespace' and group_of(obj) == ''
    }
    return sorted(named - rendered - BUILTIN_NAMESPACES)


def refusals(stderr):
    """The error lines of kubectl's stderr, without its warnings."""
    return [
        line
        for line in stderr.splitlines()
        if line.strip() and not line.startswith('Warning:') and not line.startswith('W1')
    ]


class Cluster:
    """kubectl against the throwaway cluster."""

    def __init__(self, kubectl):
        self.kubectl = shlex.split(kubectl)

    def run(self, args, stdin=None, impersonate=False):
        identity = []
        if impersonate:
            identity = [f'--as={CONTROLLER}'] + [f'--as-group={g}' for g in CONTROLLER_GROUPS]
        return subprocess.run(
            [*self.kubectl, *identity, *args],
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )

    def apply(self, objects, dry_run=False, impersonate=True):
        """Server-side apply as Argo CD does; returns the refusals."""
        args = ['apply', '--server-side', '--force-conflicts', f'--field-manager={FIELD_MANAGER}']
        if dry_run:
            args.append('--dry-run=server')
        with tempfile.NamedTemporaryFile('w', suffix='.yaml') as manifest:
            manifest.write(yaml.safe_dump_all(objects, sort_keys=False))
            manifest.flush()
            result = self.run([*args, '-f', manifest.name], impersonate=impersonate)
        if result.returncode == 0:
            return []
        return refusals(result.stderr) or [f'kubectl exited {result.returncode}']

    def must(self, args, stdin=None):
        result = self.run(args, stdin=stdin)
        if result.returncode != 0:
            raise SystemExit(f'kubectl {" ".join(args)} failed: {result.stderr.strip()}')
        return result.stdout


def controller_binding():
    return {
        'apiVersion': 'rbac.authorization.k8s.io/v1',
        'kind': 'ClusterRoleBinding',
        'metadata': {'name': 'server-dry-run-argocd'},
        'roleRef': {
            'apiGroup': 'rbac.authorization.k8s.io',
            'kind': 'ClusterRole',
            'name': 'cluster-admin',
        },
        'subjects': [{'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'User', 'name': CONTROLLER}],
    }


def canaries():
    """A validating and a mutating policy that touch only the canary ConfigMap."""
    match = {
        'resourceRules': [
            {
                'apiGroups': [''],
                'apiVersions': ['v1'],
                'operations': ['CREATE'],
                'resources': ['configmaps'],
            }
        ]
    }
    condition = [{'name': 'canary', 'expression': f"object.metadata.name == '{CANARY}'"}]
    api = 'admissionregistration.k8s.io/v1'
    return [
        {
            'apiVersion': api,
            'kind': 'ValidatingAdmissionPolicy',
            'metadata': {'name': CANARY},
            'spec': {
                'failurePolicy': 'Fail',
                'matchConstraints': match,
                'matchConditions': condition,
                'validations': [{'expression': "!has(object.data) || !('deny' in object.data)"}],
            },
        },
        {
            'apiVersion': api,
            'kind': 'ValidatingAdmissionPolicyBinding',
            'metadata': {'name': CANARY},
            'spec': {'policyName': CANARY, 'validationActions': ['Deny']},
        },
        {
            'apiVersion': api,
            'kind': 'MutatingAdmissionPolicy',
            'metadata': {'name': CANARY},
            'spec': {
                'failurePolicy': 'Fail',
                'reinvocationPolicy': 'Never',
                'matchConstraints': match,
                'matchConditions': condition,
                'mutations': [
                    {
                        'patchType': 'ApplyConfiguration',
                        'applyConfiguration': {
                            'expression': (
                                'Object{metadata: Object.metadata{'
                                f"labels: {{'{CANARY}': 'mutated'}}}}}}"
                            )
                        },
                    }
                ],
            },
        },
        {
            'apiVersion': api,
            'kind': 'MutatingAdmissionPolicyBinding',
            'metadata': {'name': CANARY},
            'spec': {'policyName': CANARY},
        },
    ]


def canary_configmap(deny):
    data = {'deny': 'yes'} if deny else {}
    return {
        'apiVersion': 'v1',
        'kind': 'ConfigMap',
        'metadata': {'name': CANARY, 'namespace': 'default'},
        'data': data,
    }


def wait_for_policies(cluster, timeout=120):
    """Until both canaries act: the policies applied before them are loaded too.

    The API server loads policies through one informer per kind, in order, so
    a canary applied after the real policies is enforced only once they are.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        denied = cluster.run(
            ['create', '--dry-run=server', '-f', '-'],
            stdin=yaml.safe_dump(canary_configmap(deny=True)),
        )
        mutated = cluster.run(
            ['create', '--dry-run=server', '-o', 'yaml', '-f', '-'],
            stdin=yaml.safe_dump(canary_configmap(deny=False)),
        )
        if denied.returncode != 0 and f'{CANARY}: mutated' in mutated.stdout:
            return
        time.sleep(2)
    raise SystemExit('the admission policies did not take effect in time')


def annotate(app, problems):
    """Print one Application's refusals, as a GitHub annotation on Actions."""
    text = '\n'.join(problems)
    if ACTIONS:
        escaped = text.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print(f'::error title=server dry run: {app}::{escaped}')
    else:
        print(f'error: {app}:\n  ' + '\n  '.join(problems), file=sys.stderr)


def run_phase(phase, apply, failures):
    """Apply each Application's share of a phase, collecting refusals per Application."""
    for entry, objects in sorted(phase.items()):
        problems = apply(objects)
        if problems:
            failures.setdefault(application_of(entry), []).extend(problems)
        print(f'{entry}: {len(objects)} objects, {len(problems)} refused')


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--kubectl', default='kubectl')
    parser.add_argument('--any-context', action='store_true')
    parser.add_argument('rendered', type=pathlib.Path)
    args = parser.parse_args(argv[1:])
    cluster = Cluster(args.kubectl)
    context = cluster.must(['config', 'current-context']).strip()
    if not context.startswith('kind-') and not args.any_context:
        raise SystemExit(f'{context} is not a kind cluster; this script writes to it')
    apps = load(args.rendered)
    phases = partition(apps)
    failures = {}

    # Argo CD's controller holds cluster-admin through the argo-cd chart.
    problems = cluster.apply([controller_binding()], impersonate=False)
    if problems:
        raise SystemExit('cannot bind the controller identity: ' + '; '.join(problems))

    print('::group::CRDs' if ACTIONS else '== CRDs')
    run_phase(phases['crds'], cluster.apply, failures)
    cluster.must(['wait', '--for=condition=Established', 'crd', '--all', '--timeout=180s'])

    print('::endgroup::\n::group::Admission policies' if ACTIONS else '== Admission policies')
    run_phase(phases['policies'], cluster.apply, failures)
    cluster.apply(canaries(), impersonate=False)
    wait_for_policies(cluster)

    print('::endgroup::\n::group::Namespaces' if ACTIONS else '== Namespaces')
    run_phase(phases['namespaces'], cluster.apply, failures)
    extra = unrendered_namespaces(apps)
    for namespace in extra:
        cluster.run(['create', 'namespace', namespace])
    if extra:
        print(f'created, rendered by no Application here: {", ".join(extra)}')

    print('::endgroup::\n::group::Dry run' if ACTIONS else '== Dry run')
    run_phase(phases['dry_run'], lambda o: cluster.apply(o, dry_run=True), failures)
    print('::endgroup::' if ACTIONS else '')

    for app, problems in sorted(failures.items()):
        annotate(app, problems)
    summary = (
        f'## Server dry run\n\n{sum(len(o) for o in apps.values())} objects from '
        f'{len(apps)} files; {len(failures)} Application(s) refused.\n'
    )
    for app, problems in sorted(failures.items()):
        summary += f'\n### {app}\n\n```text\n' + '\n'.join(problems) + '\n```\n'
    print(summary)
    if 'GITHUB_STEP_SUMMARY' in os.environ:
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as handle:
            handle.write(summary)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
