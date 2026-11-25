"""Tests for scripts/renovate-coverage.py. Only the versioning test runs Node."""

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    'renovate_coverage', ROOT / 'scripts' / 'renovate-coverage.py'
)
assert _spec and _spec.loader
rc = importlib.util.module_from_spec(_spec)
sys.modules['renovate_coverage'] = rc
_spec.loader.exec_module(rc)

DIGEST = 'sha256:' + 'a' * 64
SHA = 'b' * 40


def values(path: str, line: str) -> list[tuple[str, ...]]:
    return [v for _, v in rc.scan_line(path, line)]


@pytest.mark.parametrize(
    ('path', 'line', 'expected'),
    [
        ('payload/x/application.yaml', '    targetRevision: 1.16.0', [('1.16.0',)]),
        ('payload/x/application.yaml', '  targetRevision: v8.7.1', [('v8.7.1',)]),
        ('payload/x/application.yaml', f'  targetRevision: {SHA}', [(SHA,)]),
        ('payload/x/daemonset.yaml', f'  image: busybox:1.38.0@{DIGEST}', [('1.38.0', DIGEST)]),
        (
            'payload/x/cnpg.yaml',
            '  imageName: ghcr.io/c/p:18.7-standard-trixie',
            [('18.7-standard-trixie',)],
        ),
        ('payload/x/a.yaml', '  - registry.k8s.io/a/b=registry.k8s.io/a/b:v8.7.1', [('v8.7.1',)]),
        ('payload/x/values.yaml', '    tag: 3.4.8-alpine', [('3.4.8-alpine',)]),
        ('payload/x/a.yaml', f'  digest: {DIGEST}', [('a' * 64,)]),
        (
            '.github/workflows/a.yaml',
            f'    container: python:3.14-slim@{DIGEST}',
            [('3.14-slim', DIGEST)],
        ),
        ('.github/workflows/a.yaml', f'      - uses: actions/checkout@{SHA} # v7', [(SHA,)]),
        (
            '.github/workflows/a.yaml',
            f'  uses: docker://ghcr.io/g/g:v8.30.1@{DIGEST}',
            [('v8.30.1', DIGEST)],
        ),
        ('.github/workflows/a.yaml', '          version: "0.13.9"', [('0.13.9',)]),
        ('.pre-commit-config.yaml', '    rev: v0.24.2', [('v0.24.2',)]),
        ('ansible/inventory.yaml', '    flatcar_version: "4757.2.4"', [('4757.2.4',)]),
        ('ansible/inventory.yaml', '    syslinux_version: "6.03"', [('6.03',)]),
        ('ansible/inventory.yaml', f'    kubernetes_sha256: "{"c" * 64}"', [('c' * 64,)]),
        ('scripts/update-fonts.sh', 'INTER_VERSION="v4.1"', [('v4.1',)]),
        (
            'Makefile',
            'curl -o x https://github.com/o/r/releases/download/v1.2.0/x.yaml',
            [('v1.2.0',)],
        ),
        ('scripts/x.sh', 'npx --yes renovate@44.1.0 --platform=local', [('44.1.0',)]),
        ('pyproject.toml', '    "ansible-lint>=26.11.0",', [('26.11.0',)]),
        ('.github/tools/package.json', '    "renovate": "44.257.9"', [('44.257.9',)]),
        ('.python-version', '3.14', [('3.14',)]),
        # A digest counts in a comment: the Gateway API one is verified by make.
        (
            'payload/x/application.yaml',
            f'    # standard-install.yaml sha256:{"d" * 64}',
            [('d' * 64,)],
        ),
    ],
)
def test_scan_line_finds_pins(path: str, line: str, expected: list[tuple[str, ...]]) -> None:
    assert values(path, line) == expected


@pytest.mark.parametrize(
    ('path', 'line'),
    [
        ('payload/x/application.yaml', '    targetRevision: HEAD'),
        ('payload/x/a.yaml', 'apiVersion: apps/v1'),
        ('payload/x/a.yaml', '      engineVersion: v2'),
        ('payload/x/a.yaml', '    version: 1'),
        ('payload/x/a.yaml', '      image: docker.io/library/redis'),
        ('payload/x/a.yaml', '    # Held at 3.14 until ansible-core supports 3.15'),
        (
            'ansible/templates/a.yaml.j2',
            '  image: ghcr.io/kube-vip/kube-vip:{{ kube_vip_version }}',
        ),
        ('scripts/x.sh', 'url="https://github.com/o/r/releases/download/$VERSION/x.zip"'),
        ('.github/tools/package.json', '  "version": "1.0.0",'),
    ],
)
def test_scan_line_ignores_non_pins(path: str, line: str) -> None:
    assert rc.scan_line(path, line) == []


@pytest.mark.parametrize(
    ('path', 'line'),
    [
        ('ansible/templates/butane_node_config.yaml.j2', 'version: 1.0.0'),
        ('.pre-commit-config.yaml', 'minimum_pre_commit_version: "4.4.0"'),
        ('payload/x/grafana-dashboards.yaml', '          "pluginVersion": "10.3.1",'),
    ],
)
def test_ignore_list(path: str, line: str) -> None:
    assert rc._ignored(path, line)


def test_scan_reads_only_scanned_files(tmp_path: Path) -> None:
    (tmp_path / 'payload').mkdir()
    (tmp_path / 'payload' / 'app.yaml').write_text('a: 1\n  targetRevision: 1.2.3\n')
    (tmp_path / 'notes.yaml').write_text('targetRevision: 9.9.9\n')
    pins = rc.scan(tmp_path, ['payload/app.yaml', 'notes.yaml', 'payload/missing.yaml'])
    assert pins == [rc.Pin('payload/app.yaml', 2, 'targetRevision', ('1.2.3',))]


def package_files(*deps: dict, path: str = 'a.yaml') -> dict:
    return {'m': [{'packageFile': path, 'deps': list(deps)}]}


def test_coverage_is_per_file_and_by_whole_token() -> None:
    extracted = rc.collect(
        package_files(
            {'currentValue': 'kubernetes-v1.37.1', 'currentDigest': 'e' * 64},
            {'currentValue': '>=26.11.0', 'lockedVersion': '26.11.2'},
            {'currentValue': '3.14-slim'},
        )
    )
    assert rc.covered(rc.Pin('a.yaml', 1, 'v', ('v1.37.1',)), extracted)
    assert rc.covered(rc.Pin('a.yaml', 1, 'v', ('26.11.0',)), extracted)
    assert rc.covered(rc.Pin('a.yaml', 1, 'v', ('e' * 64,)), extracted)
    assert not rc.covered(rc.Pin('a.yaml', 1, 'v', ('3',)), extracted)
    assert not rc.covered(rc.Pin('a.yaml', 1, 'v', ('1.37',)), extracted)
    assert not rc.covered(rc.Pin('b.yaml', 1, 'v', ('3.14-slim',)), extracted)


def test_skipped_dependencies_do_not_count() -> None:
    extracted = rc.collect(
        package_files(
            {'depName': 'a', 'currentValue': 'v1.0.0', 'skipReason': 'github-token-required'},
            {'depName': 'b', 'currentValue': '2.0.0', 'skipReason': 'contains-variable'},
        )
    )
    pins = [rc.Pin('a.yaml', 1, 'tag', ('v1.0.0',)), rc.Pin('a.yaml', 2, 'tag', ('2.0.0',))]
    assert rc.check(pins, extracted) == [
        ('a.yaml', 2, 'tag pin 2.0.0: Renovate extracts it but skips it (contains-variable)')
    ]


def test_untracked_pin_is_reported() -> None:
    pins = [rc.Pin('a.yaml', 7, 'image', ('1.2.3', DIGEST))]
    assert rc.check(pins, rc.collect({})) == [
        ('a.yaml', 7, f'image pin 1.2.3 @ {DIGEST}: no Renovate manager extracts it')
    ]


RENOVATE = ROOT / rc.RENOVATE


@pytest.mark.skipif(
    not RENOVATE.exists() or not shutil.which('node'), reason='needs npm ci in .github/tools'
)
def test_versioning_check_drops_what_lookup_drops() -> None:
    """A two-part tag under strict semver is extracted and then dropped by lookup."""
    two_part = {'datasource': 'github-releases', 'currentValue': 'v4.1', 'versioning': 'semver'}
    regex = {**two_part, 'versioning': 'regex:^v(?<major>\\d+)\\.(?<minor>\\d+)$'}
    extracted = rc.collect(
        {
            'regex': [
                {'packageFile': 'i.sh', 'deps': [two_part]},
                {'packageFile': 'j.sh', 'deps': [regex]},
            ]
        },
        str(RENOVATE),
    )
    assert not rc.covered(rc.Pin('i.sh', 1, 'variable', ('v4.1',)), extracted)
    assert rc.covered(rc.Pin('j.sh', 1, 'variable', ('v4.1',)), extracted)
    assert rc.check([rc.Pin('i.sh', 1, 'variable', ('v4.1',))], extracted) == [
        (
            'i.sh',
            1,
            'variable pin v4.1: Renovate extracts it but skips it '
            '(invalid under semver versioning)',
        )
    ]
