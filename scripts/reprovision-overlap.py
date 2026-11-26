"""Fail when a reprovision Ignition config writes a path flatcar-reset keeps.

Ignition stops the boot on a file or link that already exists unless the entry
sets `overwrite`, so a kept path it writes leaves the node in the emergency
shell. Matches the way flatcar-reset does: each keep entry is an egrep regex
over the whole path, and a plain path also keeps everything below it.

Usage: reprovision-overlap.py ansible/playbooks/vars/reprovision.yaml IGNITION.json...
See docs/operations/nodes.md#reprovisioning-a-node
"""

import json
import re
import sys
from pathlib import Path

import yaml

# flatcar-reset appends PATH/.* unless the entry already ends like a regex.
REGEX_ENDINGS = ('$', ')', '*', ']', '?')


def keep_patterns(entries: list[str]) -> list[re.Pattern]:
    patterns = []
    for raw in entries:
        entry = raw.rstrip('/')
        if entry.startswith('/') and not entry.endswith(REGEX_ENDINGS):
            patterns.append(re.compile(f'{entry}/.*'))
        patterns.append(re.compile(entry))
    return patterns


def kept(path: str, patterns: list[re.Pattern]) -> bool:
    return any(p.fullmatch(path) for p in patterns)


def collisions(config: dict, patterns: list[re.Pattern]) -> list[str]:
    """Files and links the config writes over a kept path without overwrite."""
    storage = config.get('storage', {})
    entries = storage.get('files', []) + storage.get('links', [])
    return [e['path'] for e in entries if not e.get('overwrite') and kept(e['path'], patterns)]


def main() -> int:
    keep = yaml.safe_load(Path(sys.argv[1]).read_text())
    entries = keep['reprovision_keep_paths']
    patterns = keep_patterns(entries)
    # ALLOW_MINOR=1 resets with the -minor config and without these entries.
    minor = keep_patterns([e for e in entries if e not in keep.get('reprovision_minor_drop', [])])
    failed = False
    for name in sys.argv[2:]:
        checked = minor if name.endswith('-reprovision-minor.json') else patterns
        for path in collisions(json.loads(Path(name).read_text()), checked):
            print(f'::error file={name}::{path} is kept by flatcar-reset and written by Ignition')
            failed = True
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
