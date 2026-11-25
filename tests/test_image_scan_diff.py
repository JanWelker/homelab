"""Tests for the parts of scripts/image-scan-diff.py that read Applications offline."""

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'image-scan-diff.py'
spec = importlib.util.spec_from_file_location('image_scan_diff', SCRIPT)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def test_kustomize_helm_charts_become_chart_sources(tmp_path):
    (tmp_path / 'kustomization.yaml').write_text(
        'helmCharts:\n'
        '  - name: rook-ceph\n'
        '    repo: https://charts.rook.io/release\n'
        '    version: v1.21.2\n'
        '    valuesInline:\n'
        '      a: null\n'
    )
    assert scan.kustomize_charts(tmp_path) == [
        {
            'repoURL': 'https://charts.rook.io/release',
            'chart': 'rook-ceph',
            'targetRevision': 'v1.21.2',
            'helm': {'valuesObject': {'a': None}},
        }
    ]


def test_directory_without_helm_charts_has_none(tmp_path):
    assert scan.kustomize_charts(tmp_path) == []
    (tmp_path / 'kustomization.yaml').write_text('resources:\n  - a.yaml\n')
    assert scan.kustomize_charts(tmp_path) == []
