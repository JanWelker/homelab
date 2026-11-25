#!/usr/bin/env python3
"""Fail on a version pin that no Renovate manager tracks.

Runs Renovate's extraction (local platform, no token; the only request fetches
the shared preset), scans the
repository for pins on its own, and reports each pin the extraction did not
produce, or produced with a value Renovate's versioning rejects: lookup drops
those without a word. Prints GitHub `::error` annotations and exits 1 on any.
See docs/development/maintenance.md#manager-rules.

Extraction does not apply packageRules, so a `versioning` set in a packageRule
is not seen here; set it in the custom manager instead.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RENOVATE = '.github/tools/node_modules/.bin/renovate'

# A release number: 3.14, 6.03, v1.2.3, 4757.2.4, 18.7-standard-trixie.
VERSION = r'v?\d+(?:\.\d+)+(?:[-+][\w.+-]*)?'
SHA256 = r'[0-9a-f]{64}'
GIT_SHA = r'[0-9a-f]{40}'
# [registry[:port]/]path[:tag][@sha256:digest]; a port only before a slash.
IMAGE = (
    r'[a-z0-9][\w.-]*(?::\d+(?=/))?(?:/[\w.-]+)*'
    r'(?::\w[\w.-]*)?(?:@sha256:[0-9a-f]{64})?(?![\w.:@/-])'
)

# Files that can carry a pin, by repository-relative path.
SCANNED = re.compile(
    r'^(?:payload/.+\.ya?ml'
    r'|ansible/.+\.(?:ya?ml|j2)'
    r'|\.github/.+\.ya?ml'
    r'|\.github/tools/package\.json'
    r'|\.pre-commit-config\.yaml'
    r'|scripts/(?:lib/)?[^/]+\.sh'
    r'|Makefile|pyproject\.toml|\.python-version)$'
)

# Lines the scanner matches that hold no pin: (path regex, line regex, why).
IGNORED: list[tuple[str, str, str]] = [
    (
        r'^ansible/templates/butane_.*\.j2$',
        r'^version: 1\.0\.0$',
        'the Butane config spec version, a schema, not a release',
    ),
    (
        r'^\.pre-commit-config\.yaml$',
        r'^minimum_pre_commit_version:',
        'a floor for the hook runner; pre-commit itself is pinned in pyproject.toml',
    ),
    (
        r'grafana-dashboards\.yaml$',
        r'.',
        'vendored dashboard JSON: pluginVersion and schema numbers, no dependency',
    ),
]

# Skip reasons only the tokenless run produces; the hosted Renovate has a token.
OFFLINE_ONLY = {'github-token-required'}


@dataclass(frozen=True)
class Pin:
    path: str
    line: int
    kind: str
    # Each value alone identifies the pin in Renovate's output: a tag or its digest.
    values: tuple[str, ...]


def _key(names: str) -> re.Pattern[str]:
    """A YAML `key: value` line whose value is a release number."""
    return re.compile(rf'^\s*-?\s*(?:{names}):\s*["\']?({VERSION})["\']?\s*(?:#.*)?$')


YAML_RULES: list[tuple[str, re.Pattern[str]]] = [
    ('targetRevision', re.compile(rf'^\s*-?\s*targetRevision:\s*["\']?({VERSION}|{GIT_SHA})\b')),
    ('tag', _key(r'tag|newTag|rev')),
    ('version', _key(r'version|[\w-]*[_-]version|[a-z]\w*Version')),
]
# image:, imageName:, container:, docker://, and kustomize `- name=ref` images.
IMAGE_RE = re.compile(
    rf'(?:^\s*-?\s*(?:image|imageName|container):\s*["\']?|docker://|^\s*-\s*[^\s=]+=)({IMAGE})'
)
USES_RE = re.compile(r'^\s*-?\s*uses:\s*["\']?[\w.-]+/[\w./-]+@([\w.-]+)')
SHELL_VERSION_RE = re.compile(
    rf'^\s*(?:export\s+)?[A-Z][A-Z0-9_]*VERSION[A-Z0-9_]*\s*:?=\s*["\']?({VERSION})["\']?'
)
URL_VERSION_RE = re.compile(rf'/releases/download/({VERSION})/|[?&]ref=({VERSION}|{GIT_SHA})\b')
# pkg==1.2, pkg@1.2.3, @scope/pkg@1.2.3 in a command line.
SPEC_RE = re.compile(rf'(?<![\w@/.-])(?:@[\w.-]+/)?[a-z][\w.-]*(?:==|@)({VERSION})(?![\w.])')
SHA_RE = re.compile(rf'(?<![0-9a-f]){SHA256}(?![0-9a-f])')
PEP508_RE = re.compile(rf'"[A-Za-z0-9][\w.\[\],-]*\s*(?:==|>=|~=|<=|<|>)\s*({VERSION})')
NPM_RE = re.compile(rf'^\s*"(?!version")[^"]+":\s*"[\^~]?({VERSION})"')


def _ignored(path: str, line: str) -> bool:
    stripped = line.strip()
    return any(re.search(p, path) and re.search(text, stripped) for p, text, _ in IGNORED)


def _image_values(ref: str) -> tuple[str, ...]:
    """The tag and digest of an image reference; empty if it pins neither."""
    name, _, digest = ref.partition('@')
    last = name.rsplit('/', 1)[-1]
    tag = last.partition(':')[2]
    return tuple(v for v in (tag, digest) if v)


Found = list[tuple[str, tuple[str, ...]]]


def _yaml_pins(line: str) -> Found:
    found: Found = [(kind, (m.group(1),)) for kind, rule in YAML_RULES if (m := rule.match(line))]
    if (m := IMAGE_RE.search(line)) and (values := _image_values(m.group(1))):
        found.append(('image', values))
    if m := USES_RE.match(line):
        found.append(('action', (m.group(1),)))
    return found


def _code_pins(name: str, line: str) -> Found:
    """The pins on a line that is not a comment, by the file's type."""
    if name.endswith(('.yaml', '.yml', '.j2')):
        found = _yaml_pins(line)
    elif name.endswith('.sh') or name == 'Makefile':
        found = [('variable', (m.group(1),)) for m in SHELL_VERSION_RE.finditer(line)]
    elif name == 'pyproject.toml':
        return [('python', (m.group(1),)) for m in PEP508_RE.finditer(line)]
    elif name == 'package.json':
        return [('npm', (m.group(1),)) for m in NPM_RE.finditer(line)]
    elif name == '.python-version':
        return [('python', (line.strip(),))] if re.fullmatch(VERSION, line.strip()) else []
    else:
        found = []
    if not found:
        found += [('url', (m.group(1) or m.group(2),)) for m in URL_VERSION_RE.finditer(line)]
        found += [('package', (m.group(1),)) for m in SPEC_RE.finditer(line)]
    return found


def scan_line(path: str, line: str) -> Found:
    """The pins on one line, as (kind, values)."""
    name = path.rsplit('/', 1)[-1]
    code = not line.lstrip().startswith('#') and '{{' not in line
    found = _code_pins(name, line) if code else []
    # A digest is a pin wherever it stands, comments included: the Gateway API
    # sha256 sits in a comment that the Makefile verifies.
    seen = [v for _, values in found for v in values]
    for m in SHA_RE.finditer(line):
        if not any(m.group(0) in v for v in seen):
            found.append(('sha256', (m.group(0),)))
    return found


def scan(root: Path, paths: list[str]) -> list[Pin]:
    pins: list[Pin] = []
    for path in paths:
        if not SCANNED.match(path):
            continue
        try:
            text = (root / path).read_text(encoding='utf-8')
        except FileNotFoundError, UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if not _ignored(path, line):
                pins += [Pin(path, number, k, v) for k, v in scan_line(path, line)]
    return pins


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ['git', 'ls-files', '-z'], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return [p for p in out.split('\0') if p]


def run_extract(root: Path, renovate: str) -> dict:
    """Renovate's extracted packageFiles, from an offline local-platform run."""
    env = {
        **os.environ,
        'LOG_LEVEL': 'info',
        'LOG_FORMAT': 'json',
        'RENOVATE_PLATFORM': 'local',
        'RENOVATE_DRY_RUN': 'extract',
    }
    proc = subprocess.run(
        [renovate], cwd=root, env=env, capture_output=True, text=True, check=False
    )
    package_files = None
    for raw in proc.stdout.splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if entry.get('msg') == 'Extracted dependencies':
            package_files = entry.get('packageFiles')
    if package_files is None:
        sys.stderr.write(proc.stdout[-4000:] + proc.stderr[-4000:])
        raise SystemExit(f'renovate extracted nothing (exit {proc.returncode})')
    return package_files


# Lookup tests each currentValue with this versioning before it asks a registry
# and drops the dependency when it fails; ask Renovate's own modules.
NODE_VALIDATE = """
import { pathToFileURL } from 'node:url';
const dist = process.argv[1] + '/';
const { get } = await import(pathToFileURL(dist + 'modules/versioning/index.js'));
const { getDefaultVersioning } = await import(pathToFileURL(dist + 'modules/datasource/common.js'));
let input = '';
for await (const chunk of process.stdin) input += chunk;
const valid = JSON.parse(input).map((d) => {
  try {
    return get(d.versioning ?? getDefaultVersioning(d.datasource)).isValid(d.currentValue);
  } catch {
    return false;
  }
});
process.stdout.write(JSON.stringify(valid));
"""


def valid_values(deps: list[dict], renovate: str) -> list[bool]:
    """For each dep, whether Renovate's versioning accepts its currentValue."""
    # .bin/renovate sits beside the package it links to.
    dist = Path(renovate).parent.parent / 'renovate' / 'dist'
    proc = subprocess.run(
        ['node', '--input-type=module', '-e', NODE_VALIDATE, str(dist)],
        input=json.dumps(deps),
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode:
        raise SystemExit(f'renovate versioning check failed: {proc.stderr[-2000:]}')
    return json.loads(proc.stdout)


@dataclass
class Extracted:
    # path -> every value Renovate tracks there (currentValue, currentDigest, ...)
    values: dict[str, set[str]]
    # (path, dep, reason) for each dependency Renovate extracts but will not look up
    dropped: list[tuple[str, dict, str]]


def collect(package_files: dict, renovate: str | None = None) -> Extracted:
    """What Renovate tracks, per file; with `renovate`, also check versioning."""
    kept: list[tuple[str, dict]] = []
    dropped: list[tuple[str, dict, str]] = []
    for manager_files in package_files.values():
        for package_file in manager_files:
            path = package_file['packageFile']
            for dep in package_file.get('deps', []):
                reason = dep.get('skipReason')
                if reason and reason not in OFFLINE_ONLY:
                    dropped.append((path, dep, reason))
                else:
                    kept.append((path, dep))
    if renovate:
        # Lookup keeps a dependency whose value it cannot parse only for its digest.
        unversioned = [
            (path, dep)
            for path, dep in kept
            if isinstance(dep.get('currentValue'), str)
            and not (dep.get('currentDigest') or dep.get('lockedVersion'))
        ]
        verdicts = valid_values([dep for _, dep in unversioned], renovate) if unversioned else []
        for (path, dep), ok in zip(unversioned, verdicts, strict=True):
            if not ok:
                kept.remove((path, dep))
                versioning = dep.get('versioning', 'its default')
                dropped.append((path, dep, f'invalid under {versioning} versioning'))
    values: dict[str, set[str]] = {}
    for path, dep in kept:
        tracked = values.setdefault(path, set())
        for key in ('currentValue', 'currentDigest', 'lockedVersion', 'replaceString'):
            if isinstance(dep.get(key), str):
                tracked.add(dep[key])
    return Extracted(values, dropped)


def _contains(haystack: str, value: str) -> bool:
    """`value` as a whole token of `haystack`: v1.37.1 in kubernetes-v1.37.1, not 3 in 3.14."""
    return re.search(rf'(?<![\w.]){re.escape(value)}(?![\w.])', haystack) is not None


def covered(pin: Pin, extracted: Extracted) -> bool:
    tracked = extracted.values.get(pin.path, set())
    return any(_contains(t, v) for v in pin.values for t in tracked)


def check(pins: list[Pin], extracted: Extracted) -> list[tuple[str, int, str]]:
    """(path, line, message) for each untracked pin."""
    errors = []
    for pin in pins:
        if covered(pin, extracted):
            continue
        reasons = sorted(
            {
                reason
                for path, dep, reason in extracted.dropped
                if path == pin.path
                and any(
                    _contains(str(dep.get(k, '')), v)
                    for v in pin.values
                    for k in ('currentValue', 'currentDigest')
                )
            }
        )
        why = (
            f'Renovate extracts it but skips it ({", ".join(reasons)})'
            if reasons
            else ('no Renovate manager extracts it')
        )
        errors.append((pin.path, pin.line, f'{pin.kind} pin {" @ ".join(pin.values)}: {why}'))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', nargs='?', default='.', type=Path)
    parser.add_argument('--renovate', help=f'Renovate binary (default: ROOT/{RENOVATE})')
    parser.add_argument('--extract-json', type=Path, help='packageFiles JSON instead of a run')
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    renovate = args.renovate or str(root / RENOVATE)

    if args.extract_json:
        package_files = json.loads(args.extract_json.read_text())
    else:
        package_files = run_extract(root, renovate)
    extracted = collect(package_files, renovate if Path(renovate).exists() else None)
    pins = scan(root, tracked_files(root))
    errors = check(pins, extracted)

    for path, line, message in errors:
        print(f'::error file={path},line={line},title=Untracked pin::{message}')
    print(f'{len(pins)} pins, {len(errors)} untracked by Renovate', file=sys.stderr)
    if errors:
        print('Fix: docs/development/maintenance.md#manager-rules', file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
