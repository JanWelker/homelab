"""Tests for scripts/docs-length.py: the word count and the growth comparison."""

import importlib.util
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / 'scripts' / 'docs-length.py'
spec = importlib.util.spec_from_file_location('docs_length', SCRIPT)
docs_length = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs_length)


@pytest.mark.parametrize(
    ('data', 'words'),
    [
        (b'', 0),
        (b'   \n\t\n', 0),
        (b'one', 1),
        (b'one two\nthree\ttwo  four\r\n', 5),
        (b'# Title\n\n| a | b |\n| --- | --- |\n', 12),
        ('café über'.encode(), 2),
    ],
)
def test_count_words_matches_wc(data, words):
    assert docs_length.count_words(data) == words


@pytest.mark.parametrize(
    ('path', 'covered'),
    [
        ('docs/quickstart.md', True),
        ('docs/platform/openbao.md', True),
        ('docs/operations/triage-2026-09-20.md', False),
        ('docs/operations/network-policy-audit-2026-10-16.md', False),
        ('docs/assets/fonts/readme.txt', False),
        ('docs/assets/javascripts/x.js', False),
        ('README.md', False),
        ('payload/docs/x.md', False),
    ],
)
def test_is_page(path, covered):
    assert docs_length.is_page(path) is covered


def test_grown_reports_only_pages_that_got_longer():
    base = {'docs/a.md': 10, 'docs/b.md': 10, 'docs/c.md': 10, 'docs/gone.md': 5}
    head = {'docs/a.md': 11, 'docs/b.md': 10, 'docs/c.md': 4, 'docs/new.md': 900}
    assert docs_length.grown(base, head) == [('docs/a.md', 10, 11)]


def test_grown_is_sorted_by_path():
    base = {'docs/z.md': 1, 'docs/a.md': 1}
    head = {'docs/z.md': 2, 'docs/a.md': 3}
    assert docs_length.grown(base, head) == [('docs/a.md', 1, 3), ('docs/z.md', 1, 2)]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository whose main branch holds two pages and a dated log."""
    if not shutil.which('git'):
        pytest.skip('needs git, which the Lint Python container lacks')
    monkeypatch.chdir(tmp_path)

    def run(*args):
        subprocess.run(['git', *args], check=True, capture_output=True)

    run('init', '-b', 'main')
    run('config', 'user.email', 'test@example.invalid')
    run('config', 'user.name', 'Test')
    (tmp_path / 'docs' / 'platform').mkdir(parents=True)
    (tmp_path / 'docs' / 'a.md').write_text('one two three\n')
    (tmp_path / 'docs' / 'platform' / 'b.md').write_text('one two\n')
    (tmp_path / 'docs' / 'log-2026-01-02.md').write_text('one\n')
    run('add', '-A')
    run('commit', '-m', 'base')
    run('checkout', '-b', 'topic')
    return tmp_path, run


def test_main_passes_when_pages_shrink_or_are_new(repo, capsys):
    root, _ = repo
    (root / 'docs' / 'a.md').write_text('one\n')
    (root / 'docs' / 'brand-new.md').write_text('many many many many words\n')
    (root / 'docs' / 'log-2026-01-02.md').write_text('one two three four\n')
    assert docs_length.main(['main']) == 0
    assert capsys.readouterr().out == ''


def test_main_fails_with_annotation_on_growth(repo, capsys):
    root, _ = repo
    (root / 'docs' / 'platform' / 'b.md').write_text('one two three\n')
    assert docs_length.main(['main']) == 1
    out = capsys.readouterr().out
    assert out.startswith('::error file=docs/platform/b.md::')
    assert 'from 2 to 3 words' in out


def test_main_allow_growth_turns_the_error_into_a_notice(repo, capsys):
    root, _ = repo
    (root / 'docs' / 'a.md').write_text('one two three four\n')
    assert docs_length.main(['main', '--allow-growth']) == 0
    assert capsys.readouterr().out.startswith('::notice file=docs/a.md::')


def test_main_compares_committed_head_and_ignores_base_only_changes(repo, capsys):
    root, run = repo
    (root / 'docs' / 'a.md').write_text('one two three four five\n')
    run('commit', '-am', 'grow on the topic')
    run('checkout', 'main')
    # The base branch moves on; the topic does not touch this page.
    (root / 'docs' / 'platform' / 'b.md').write_text('one two three four five six\n')
    run('commit', '-am', 'grow on main')
    run('checkout', 'topic')
    assert docs_length.main(['main', 'topic']) == 1
    out = capsys.readouterr().out
    assert 'docs/a.md' in out
    assert 'b.md' not in out
