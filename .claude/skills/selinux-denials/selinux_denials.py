#!/usr/bin/env python3
"""Group the SELinux denials Loki holds into candidate rules.

    cd .claude/skills/selinux-denials
    uv run python selinux_denials.py rules --since 7d
    uv run python selinux_denials.py rules --since 7d --comm tetragon
    uv run python selinux_denials.py paths --since 24h --comm ceph-exporter
    uv run python selinux_denials.py te --since 7d --comm tetragon --module tetragon_local

Loki is read through the API server's service proxy, so the current kubeconfig
is all it needs. Nothing here writes to a node, the repository or the cluster:
`te` prints a type-enforcement module to review, not a policy to load.
"""

import argparse
import collections
import json
import re
import subprocess
import sys
import urllib.parse

LOKI = '/api/v1/namespaces/logging/services/loki:3100/proxy/loki/api/v1'
SELECTOR = '{job="systemd-journal"%s} |= "avc:" |= "denied"'
# One denial line: perms, command, source type, target type, object class.
PARSE = (
    'regexp `denied\\s+\\{ (?P<perms>[^}]+) \\}.*comm="(?P<comm>[^"]+)"'
    '.*scontext=[^:]+:[^:]+:(?P<stype>[a-z0-9_]+)'
    '.*tcontext=[^:]+:[^:]+:(?P<ttype>[a-z0-9_]+)'
    '.*tclass=(?P<tclass>[a-z0-9_]+)`'
)
PATH_RE = re.compile(r'(?:path|name)="([^"]+)"')
NORMALISE = (
    (re.compile(r'/(procRoot|proc|host/proc)/\d+'), r'/\1/<pid>'),
    (re.compile(r'^\d+$'), '<pid>'),
    (re.compile(r'/var/log/pods/[^/]+/'), '/var/log/pods/<pod>/'),
    (re.compile(r'journal/[0-9a-f]{32}'), 'journal/<id>'),
    (re.compile(r'\.\d+\.\d{6,}\.asok'), '.<n>.asok'),
)


def loki(endpoint, params):
    query = urllib.parse.urlencode(params)
    out = subprocess.run(
        ['kubectl', 'get', '--raw', f'{LOKI}/{endpoint}?{query}'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)['data']['result']


def selector(args):
    labels = f',node="{args.node}"' if args.node else ''
    sel = SELECTOR % labels
    if args.comm:
        sel += f' |= `comm="{args.comm}"`'
    return sel


def grouped(args):
    """{(stype, ttype, tclass): {"perms": set, "comms": set, "nodes": set, "count": int}}"""
    query = (
        f'sum by (comm, stype, ttype, tclass, perms) '
        f'(count_over_time({selector(args)} | {PARSE} [{args.since}]))'
    )
    rules = collections.defaultdict(lambda: {'perms': set(), 'comms': set(), 'count': 0})
    for series in loki('query', {'query': query}):
        m = series['metric']
        if not all(k in m for k in ('stype', 'ttype', 'tclass', 'perms')):
            continue
        rule = rules[(m['stype'], m['ttype'], m['tclass'])]
        rule['perms'].update(m['perms'].split())
        rule['comms'].add(m.get('comm', '?'))
        rule['count'] += int(float(series['value'][1]))
    return rules


def cmd_rules(args):
    rules = grouped(args)
    if not rules:
        sys.exit('No denials in that window. Is the audit filter open? See SKILL.md, step 1.')
    total = sum(r['count'] for r in rules.values())
    print(f'# {total} denial lines, {len(rules)} distinct rules, last {args.since}')
    print('# Counts are log lines: permissive mode logs a denial once per AVC cache entry.')
    for key, rule in sorted(rules.items(), key=lambda kv: (sorted(kv[1]['comms']), kv[0])):
        stype, ttype, tclass = key
        print(f'allow {stype} {ttype}:{tclass} {{ {" ".join(sorted(rule["perms"]))} }};')
        comms = ', '.join(sorted(rule['comms']))
        print(f'    # {rule["count"]}x  comm: {comms}')


def cmd_paths(args):
    seen = collections.Counter()
    lines = loki('query_range', {'query': selector(args), 'since': args.since, 'limit': args.limit})
    for stream in lines:
        for _, line in stream['values']:
            m = PATH_RE.search(line)
            ttype = re.search(r'tcontext=[^:]+:[^:]+:([a-z0-9_]+)', line)
            if not m or not ttype:
                continue
            path = m.group(1)
            for pattern, repl in NORMALISE:
                path = pattern.sub(repl, path)
            seen[(ttype.group(1), path)] += 1
    for (ttype, path), count in sorted(seen.items()):
        print(f'{ttype:24} {path}  ({count}x)')


def cmd_te(args):
    if not args.comm:
        sys.exit('te needs --comm: a module per workload, never one for every container_t process.')
    rules = grouped(args)
    types = sorted({t for stype, ttype, _ in rules for t in (stype, ttype)})
    classes = collections.defaultdict(set)
    for (_, _, tclass), rule in rules.items():
        classes[tclass].update(rule['perms'])
    print(f'module {args.module} 1.0;\n\nrequire {{')
    for t in types:
        print(f'    type {t};')
    for tclass, perms in sorted(classes.items()):
        print(f'    class {tclass} {{ {" ".join(sorted(perms))} }};')
    print('}\n')
    print(f'# From denials of comm={args.comm} over {args.since}. The source type is the')
    print('# one the process ran as; move the workload to its own type before loading')
    print('# this, or every process of that type gets these rights.')
    for (stype, ttype, tclass), rule in sorted(rules.items()):
        print(f'allow {stype} {ttype}:{tclass} {{ {" ".join(sorted(rule["perms"]))} }};')


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest='cmd', required=True)
    for name, func in (('rules', cmd_rules), ('paths', cmd_paths), ('te', cmd_te)):
        p = sub.add_parser(name)
        p.add_argument('--since', default='24h', help='Loki duration, e.g. 6h, 7d')
        p.add_argument('--node', help='one node only')
        p.add_argument('--comm', help="one command name only, as in the denial's comm= field")
        p.add_argument('--limit', type=int, default=5000, help='paths: log lines to read')
        p.add_argument('--module', default='local_workload', help='te: module name')
        p.set_defaults(func=func)
    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
