"""Tests for scripts/reprovision-overlap.py and the keep list it checks."""

import importlib.util
import json
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).parent.parent
SCRIPT = ROOT / 'scripts' / 'reprovision-overlap.py'
KEEP = ROOT / 'ansible' / 'playbooks' / 'vars' / 'reprovision.yaml'
spec = importlib.util.spec_from_file_location('reprovision_overlap', SCRIPT)
overlap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlap)

PATTERNS = overlap.keep_patterns(yaml.safe_load(KEEP.read_text())['reprovision_keep_paths'])


@pytest.mark.parametrize(
    ('path', 'is_kept'),
    [
        ('/var/lib/etcd', True),
        ('/var/lib/etcd/member/snap/db', True),
        ('/var/lib/etcdx', False),
        ('/etc/kubernetes/admin.conf', True),
        ('/etc/kubernetes/kubelet.conf', True),
        ('/etc/kubernetes/pki/etcd/ca.crt', True),
        ('/etc/kubernetes/manifests/etcd.yaml', True),
        ('/etc/kubernetes/manifests/kube-apiserver.yaml', True),
        # The bootstrap copy and everything Ignition renders go.
        ('/etc/kubernetes/manifests/kube-vip.yaml', False),
        ('/etc/kubernetes/enc/encryption-config.yaml', False),
        ('/etc/kubernetes/patches/etcd+strategic.yaml', False),
        ('/etc/kubernetes/audit/policy.yaml', False),
        ('/etc/ssh/ssh_host_ed25519_key', False),
        ('/etc/sysupdate.kubernetes.d/kubernetes.conf', False),
        ('/opt/extensions/kubernetes/kubernetes-v1.37.2-x86-64.raw', True),
        ('/etc/extensions/kubernetes.raw', True),
        ('/etc/extensions/docker-flatcar.raw', False),
        ('/opt/bootstrap-k8s.env', False),
    ],
)
def test_keep_list_matches_like_flatcar_reset(path, is_kept):
    assert overlap.kept(path, PATTERNS) is is_kept


def test_regex_entries_get_no_contents_suffix():
    patterns = overlap.keep_patterns(['/var/log', '/etc/x/[^/]+\\.conf$', '/opt/a/'])
    assert [p.pattern for p in patterns] == [
        '/var/log/.*',
        '/var/log',
        '/etc/x/[^/]+\\.conf$',
        '/opt/a/.*',
        '/opt/a',
    ]


def test_keep_entries_all_keep_their_contents():
    """reprovision.yaml's shell check appends /.* to every entry, as flatcar-reset
    does only for entries not ending like a regex; the two agree while none does."""
    entries = yaml.safe_load(KEEP.read_text())['reprovision_keep_paths']
    assert [e for e in entries if e.rstrip('/').endswith(overlap.REGEX_ENDINGS)] == []


def test_collisions_ignore_overwrite_and_unkept_paths():
    config = {
        'storage': {
            'files': [
                {'path': '/var/lib/kubelet/config.yaml'},
                {'path': '/etc/hostname'},
            ],
            'links': [
                {'path': '/etc/extensions/kubernetes.raw', 'overwrite': True},
                {'path': '/etc/kubernetes/admin.conf'},
            ],
        }
    }
    assert overlap.collisions(config, PATTERNS) == [
        '/var/lib/kubelet/config.yaml',
        '/etc/kubernetes/admin.conf',
    ]


def test_main_reports_a_collision(tmp_path, monkeypatch, capsys):
    ign = tmp_path / 'ignition.json'
    ign.write_text(json.dumps({'storage': {'files': [{'path': '/var/log/x'}]}}))
    monkeypatch.setattr('sys.argv', ['x', str(KEEP), str(ign)])
    assert overlap.main() == 1
    assert '/var/log/x' in capsys.readouterr().out
