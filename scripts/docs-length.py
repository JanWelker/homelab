#!/usr/bin/env python3
"""Fail when an edit makes an existing docs page longer.

CLAUDE.md: "an edit to an existing page leaves it the same length or shorter".
Counts the words of every page under docs/ the way `wc -w` does, on the merge
base with <base> and on <head>, and reports each page that grew.

    docs-length.py <base> [<head>] [--allow-growth]

<base> and <head> are git revisions; <head> defaults to the working tree, so
`uv run scripts/docs-length.py origin/main` checks the branch as it stands.
A page that is new passes, and so does a dated log (a name with a YYYY-MM-DD
date, like triage-2026-09-20.md). --allow-growth reports the growth as a
notice instead of failing: the pull request label `docs-growth`, for a new
component that has to document itself on pages that already exist.
"""

import argparse
import pathlib
import re
import subprocess
import sys

DOCS = 'docs'
DATED_LOG_RE = re.compile(r'\d{4}-\d{2}-\d{2}')


def count_words(data):
    """Words in bytes, as `wc -w` counts them: runs of ASCII whitespace separate them."""
    return len(data.split())


def is_page(path):
    """Whether `path` is a page the rule covers: a Markdown file under docs/, no dated log."""
    parts = pathlib.PurePosixPath(path).parts
    name = parts[-1]
    return (
        len(parts) > 1
        and parts[0] == DOCS
        and name.endswith('.md')
        and not DATED_LOG_RE.search(name)
    )


def grown(base, head):
    """Pages in both mappings (path -> word count) that are longer in `head`.

    Returns (path, base count, head count) tuples sorted by path. A page that
    exists only in `head` is new and one that exists only in `base` is gone;
    neither counts as growth.
    """
    return [
        (path, base[path], head[path])
        for path in sorted(base.keys() & head.keys())
        if head[path] > base[path]
    ]


def git(*args):
    return subprocess.run(['git', *args], check=True, capture_output=True).stdout


def counts_at(rev):
    """Word counts of every covered page at a git revision."""
    paths = git('ls-tree', '-r', '--name-only', '-z', rev, DOCS).decode().split('\0')
    return {p: count_words(git('show', f'{rev}:{p}')) for p in paths if p and is_page(p)}


def counts_on_disk():
    """Word counts of every covered page in the working tree."""
    return {
        p.as_posix(): count_words(p.read_bytes())
        for p in sorted(pathlib.Path(DOCS).rglob('*.md'))
        if is_page(p.as_posix())
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('base', help='git revision the pull request branches from')
    parser.add_argument('head', nargs='?', help='git revision to check (default: working tree)')
    parser.add_argument('--allow-growth', action='store_true', help='report growth as a notice')
    args = parser.parse_args(argv)

    # The merge base, so a page that changed only on the base branch is not blamed on the PR.
    base_rev = git('merge-base', args.base, args.head or 'HEAD').decode().strip()
    base = counts_at(base_rev)
    head = counts_at(args.head) if args.head else counts_on_disk()

    growth = grown(base, head)
    level = 'notice' if args.allow_growth else 'error'
    for path, before, after in growth:
        print(
            f'::{level} file={path}::{path} grew from {before} to {after} words (+{after - before})'
        )
    if growth and not args.allow_growth:
        print(
            'An edit to an existing page leaves it the same length or shorter '
            '(CLAUDE.md, Writing docs). A new component may grow the docs: '
            'label the pull request docs-growth.',
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
