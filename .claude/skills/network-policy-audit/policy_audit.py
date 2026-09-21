#!/usr/bin/env python3
"""Turn Hubble AUDIT verdicts into CiliumNetworkPolicy rules, and find
policies that can be consolidated.

    uv run python .claude/skills/network-policy-audit/policy_audit.py fetch --since 7d > audit.jsonl
    uv run python .claude/skills/network-policy-audit/policy_audit.py propose audit.jsonl
    uv run python .claude/skills/network-policy-audit/policy_audit.py consolidate

`fetch` reads Loki through a port-forward (default http://localhost:13100);
`propose` also accepts `hubble observe --verdict AUDIT -o json` output.
Nothing here writes to the repository or the cluster: the output is YAML
to review and paste into the policy file it names.
"""

import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import yaml

PLATFORM_DIR = "payload/platform/security/network-policies"
APPS_REPO = os.environ.get("HOMELAB_APPS", os.path.join(os.path.dirname(os.getcwd()), "homelab-apps"))
NS_LABEL = "k8s:io.kubernetes.pod.namespace"
IDENT_LABELS = ("k8s:app.kubernetes.io/name", "k8s:app", "k8s:k8s-app", "k8s:cnpg.io/podRole")
RESERVED = {1: "host", 2: "world", 3: "unmanaged", 4: "health", 5: "init", 6: "remote-node", 7: "kube-apiserver", 8: "ingress", 9: "world", 10: "world"}
_IDENTITIES = None


def identity_labels(ident):
    """Labels of a numeric Cilium identity, from the CiliumIdentity objects (read once)."""
    global _IDENTITIES
    if ident is None:
        return []
    if ident in RESERVED:
        return ["reserved:" + RESERVED[ident]]
    if _IDENTITIES is None:
        _IDENTITIES = {}
        try:
            out = subprocess.run(["kubectl", "get", "ciliumidentity", "-o", "json"], capture_output=True, text=True, check=True).stdout
            for item in json.loads(out)["items"]:
                _IDENTITIES[int(item["metadata"]["name"])] = [f"{k}={v}" for k, v in (item.get("security-labels") or {}).items()]
        except (subprocess.CalledProcessError, FileNotFoundError, KeyError, ValueError):
            print("warning: could not read CiliumIdentity objects; pod peers will be namespace-only", file=sys.stderr)
    return _IDENTITIES.get(ident, [])


KNOWN_NOISE = (
    # The Rook operator dials two stale mgr addresses; STALE_OR_UNROUTABLE_IP, not policy.
    ("rook-ceph", "rook-ceph-operator", 6800),
)


# ---------------------------------------------------------------- fetch

def fetch(args):
    seconds = {"h": 3600, "d": 86400, "m": 60}[args.since[-1]] * int(args.since[:-1])
    start = int(time.time() - seconds) * 10**9
    end = int(time.time()) * 10**9
    query = '{job="hubble", verdict="AUDIT"}'
    seen = 0
    while True:
        params = urllib.parse.urlencode({"query": query, "start": start, "end": end, "limit": 5000, "direction": "forward"})
        with urllib.request.urlopen(f"{args.loki}/loki/api/v1/query_range?{params}") as resp:
            data = json.load(resp)
        entries = sorted((int(ts), line) for stream in data["data"]["result"] for ts, line in stream["values"])
        for ts, line in entries:
            sys.stdout.write(line.rstrip("\n") + "\n")
        seen += len(entries)
        if len(entries) < 5000:
            break
        start = entries[-1][0] + 1
    print(f"{seen} audit entries", file=sys.stderr)


# ---------------------------------------------------------------- flows

def load_flows(path):
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        flow = rec.get("flow", rec)
        if flow.get("verdict") not in (None, "AUDIT"):
            continue
        if flow.get("is_reply"):
            continue
        if "is_reply" not in flow:
            # Exports written before is_reply joined the field mask: a packet
            # to an ephemeral port on a pod is the answer to a connection the
            # policy load re-evaluated, not a request.
            proto, port = port_of(flow)
            if port and port >= 32768 and (flow.get("destination") or {}).get("namespace"):
                continue
        yield flow


def endpoint(ep):
    """(namespace, identifying label dict, reserved entities) of a flow endpoint."""
    labels = ep.get("labels") or identity_labels(ep.get("identity"))
    ns = ep.get("namespace")
    entities = [l.split(":", 1)[1] for l in labels if l.startswith("reserved:") and l != "reserved:unknown"]
    ident = {}
    for key in IDENT_LABELS:
        for l in labels:
            if l.startswith(key + "="):
                ident[key] = l.split("=", 1)[1]
                break
        if ident:
            break
    wl = (ep.get("workloads") or [{}])[0].get("name") or ep.get("pod_name") or ""
    return ns, ident, entities, wl


def port_of(flow):
    l4 = flow.get("l4") or {}
    for proto in ("TCP", "UDP", "SCTP"):
        if proto in l4:
            return proto, l4[proto].get("destination_port")
    if "ICMPv4" in l4 or "ICMPv6" in l4:
        return "ICMP", None
    return None, None


def summarise(flows):
    """Group audited flows into (namespace, direction, peer, port) with counts."""
    groups = collections.Counter()
    samples = {}
    for flow in flows:
        direction = flow.get("traffic_direction")
        src_ns, src_ident, src_ent, src_wl = endpoint(flow.get("source") or {})
        dst_ns, dst_ident, dst_ent, dst_wl = endpoint(flow.get("destination") or {})
        proto, port = port_of(flow)
        names = ",".join(flow.get("destination_names") or [])
        dst_ip = (flow.get("IP") or {}).get("destination", "")
        if direction == "EGRESS" and src_ns:
            if (src_ns, src_wl, port) in KNOWN_NOISE:
                continue
            peer = ("endpoint", dst_ns, tuple(sorted(dst_ident.items()))) if dst_ns else ("entity", tuple(sorted(dst_ent)), names or dst_ip)
            key = (src_ns, "egress", src_wl, tuple(sorted(src_ident.items())), peer, proto, port)
        elif direction == "INGRESS" and dst_ns:
            peer = ("endpoint", src_ns, tuple(sorted(src_ident.items()))) if src_ns else ("entity", tuple(sorted(src_ent)), "")
            key = (dst_ns, "ingress", dst_wl, tuple(sorted(dst_ident.items())), peer, proto, port)
        else:
            continue
        groups[key] += 1
        samples.setdefault(key, flow)
    return groups, samples


# ---------------------------------------------------------------- existing policies

def policy_files():
    files = sorted(glob.glob(os.path.join(PLATFORM_DIR, "*.yaml")))
    files += sorted(glob.glob(os.path.join(APPS_REPO, "*", "networkpolicy.yaml")))
    return files


def load_policies():
    out = []
    for path in policy_files():
        for doc in yaml.safe_load_all(open(path)):
            if doc and doc.get("kind") == "CiliumNetworkPolicy":
                out.append((path, doc))
    return out


def rule_allows(rule, peer, port, proto, direction):
    """Approximate: does this rule admit the peer on the port?"""
    kind = peer[0]
    key = "fromEndpoints" if direction == "ingress" else "toEndpoints"
    ekey = "fromEntities" if direction == "ingress" else "toEntities"
    matched = False
    if kind == "endpoint":
        ns, ident = peer[1], dict(peer[2])
        for sel in rule.get(key) or []:
            ml = sel.get("matchLabels") or {}
            if not ml and not sel.get("matchExpressions"):
                matched = True  # {} = same namespace, caller checks ns
            elif ml.get(NS_LABEL) in (None, ns) and all(ml.get(k, v) == v for k, v in ident.items() if k in ml):
                if ml.get(NS_LABEL) == ns or NS_LABEL not in ml:
                    matched = True
        for sel in rule.get(key) or []:
            for ex in sel.get("matchExpressions") or []:
                if ex.get("key") == NS_LABEL and ex.get("operator") == "Exists":
                    matched = True
    else:
        entities = set(peer[1])
        allowed = set(rule.get(ekey) or [])
        if allowed & entities or "all" in allowed or ("cluster" in allowed and entities & {"host", "remote-node", "health", "init"}):
            matched = True
        if "world" in entities and (rule.get("toFQDNs") or rule.get("toCIDR") or rule.get("toCIDRSet")):
            matched = None  # a name or CIDR rule exists; needs a human look
    if not matched:
        return matched
    ports = rule.get("toPorts")
    if not ports:
        return matched
    for tp in ports:
        for p in tp.get("ports") or []:
            if str(p.get("port")) == str(port) and p.get("protocol", "ANY") in ("ANY", proto):
                return matched
    return False


def already_allowed(policies, ns, direction, ident, peer, port, proto):
    for path, pol in policies:
        if pol["metadata"].get("namespace") != ns:
            continue
        sel = (pol["spec"].get("endpointSelector") or {}).get("matchLabels") or {}
        if any(dict(ident).get("k8s:" + k) not in (None, v) for k, v in sel.items()):
            continue
        for rule in pol["spec"].get(direction) or []:
            if peer[0] == "endpoint" and peer[1] != ns and any(not (s.get("matchLabels") or s.get("matchExpressions")) for s in rule.get("fromEndpoints" if direction == "ingress" else "toEndpoints") or []):
                continue
            verdict = rule_allows(rule, peer, port, proto, direction)
            if verdict:
                return pol["metadata"]["name"]
            if verdict is None:
                return pol["metadata"]["name"] + " (name or CIDR rule; check it)"
    return None


# ---------------------------------------------------------------- propose

def snippet(direction, peer, proto, port, ns):
    key = "fromEndpoints" if direction == "ingress" else "toEndpoints"
    ekey = "fromEntities" if direction == "ingress" else "toEntities"
    rule = {}
    if peer[0] == "endpoint":
        labels = {NS_LABEL: peer[1]} if peer[1] != ns else {}
        labels.update(dict(peer[2]))
        rule[key] = [{"matchLabels": labels} if labels else {}]
    else:
        entities, names = list(peer[1]), peer[2]
        if "world" in entities and names and not re.match(r"^[0-9.]+$", names):
            rule["toFQDNs"] = [{"matchName": n} for n in names.split(",")]
        else:
            rule[ekey] = entities or ["world"]
    if port:
        rule["toPorts"] = [{"ports": [{"port": str(port), "protocol": proto}]}]
    return rule


def propose(args):
    policies = load_policies()
    groups, samples = summarise(load_flows(args.file))
    by_ns = collections.defaultdict(list)
    for key, count in sorted(groups.items(), key=lambda kv: -kv[1]):
        ns, direction, wl, ident, peer, proto, port = key
        allowed = already_allowed(policies, ns, direction, ident, peer, port, proto)
        by_ns[(ns, direction)].append((count, wl, ident, peer, proto, port, allowed))
    platform = {os.path.basename(p)[:-5] for p in glob.glob(os.path.join(PLATFORM_DIR, "*.yaml"))}
    for (ns, direction), rows in sorted(by_ns.items()):
        target = f"{PLATFORM_DIR}/{ns}.yaml" if ns in platform else f"homelab-apps/{ns}/networkpolicy.yaml"
        print(f"\n## {ns} {direction}  ->  {target}")
        for count, wl, ident, peer, proto, port, allowed in rows:
            who = f"{wl or '?'}" + (f" ({', '.join(f'{k}={v}' for k, v in ident)})" if ident else "")
            peer_desc = f"{peer[1]}/{dict(peer[2]).get('k8s:app.kubernetes.io/name') or dict(peer[2]).get('k8s:app') or dict(peer[2]).get('k8s:k8s-app') or '*'}" if peer[0] == "endpoint" else f"{','.join(peer[1]) or 'unknown'} {peer[2]}".strip()
            print(f"\n# {count}x {direction}: {who} <-> {peer_desc} {proto or ''} {port or ''}")
            if allowed:
                print(f"#   already allowed by policy '{allowed}': a reply packet at policy load, or an L7 refusal; check the L7 records before adding anything")
                continue
            print(f"#   selector for the {'target' if direction == 'ingress' else 'source'} workload: {dict(ident) or '{} (whole namespace)'}")
            if peer[0] == "entity" and "world" in peer[1] and peer[2] and re.match(r"^[0-9.]+$", peer[2]):
                print(f"#   the destination is an address, {peer[2]}: name it with toFQDNs (destination_names is empty when the DNS proxy did not see the lookup) or toCIDR, not the world entity")
            print(yaml.safe_dump([snippet(direction, peer, proto, port, ns)], sort_keys=False).rstrip())
    if not groups:
        print("no audited flows")


# ---------------------------------------------------------------- consolidate

def canon(obj):
    return json.dumps(obj, sort_keys=True)


def peer_of(rule, direction):
    keys = ("fromEndpoints", "fromEntities", "fromCIDR", "fromCIDRSet") if direction == "ingress" else ("toEndpoints", "toEntities", "toCIDR", "toCIDRSet", "toFQDNs")
    return canon({k: rule[k] for k in keys if k in rule})


def consolidate(args):
    policies = load_policies()
    findings = []
    by_ns = collections.defaultdict(list)
    for path, pol in policies:
        by_ns[pol["metadata"]["namespace"]].append((path, pol))
    for ns, pols in sorted(by_ns.items()):
        # 1. same endpointSelector twice -> one policy
        by_sel = collections.defaultdict(list)
        for path, pol in pols:
            by_sel[canon(pol["spec"].get("endpointSelector"))].append(pol["metadata"]["name"])
        for sel, names in by_sel.items():
            # default-ingress and default-egress are kept apart on purpose:
            # the security Application never prunes, so a rename would leave
            # the old object behind.
            if len(names) > 1 and sorted(names) != ["default-egress", "default-ingress"]:
                findings.append((ns, f"policies {names} select the same endpoints ({sel}); one policy with both directions reads as one unit"))
        default = {}
        for path, pol in pols:
            if pol["spec"].get("endpointSelector") == {}:
                for direction in ("ingress", "egress"):
                    for rule in pol["spec"].get(direction) or []:
                        default[(direction, canon(rule))] = pol["metadata"]["name"]
        for path, pol in pols:
            name = pol["metadata"]["name"]
            for direction in ("ingress", "egress"):
                rules = pol["spec"].get(direction) or []
                # 2. same peer, different ports -> one rule with a port list
                by_peer = collections.defaultdict(list)
                for i, rule in enumerate(rules):
                    by_peer[(peer_of(rule, direction), canon(rule.get("toPorts", [{}])[0].get("rules")))].append(i)
                for (peer, l7), idx in by_peer.items():
                    if len(idx) > 1:
                        findings.append((ns, f"{name} {direction}: rules {idx} share the peer {peer} and could be one rule with all their ports"))
                # 3. rule duplicated in a default-* policy of the same namespace
                if pol["spec"].get("endpointSelector") != {}:
                    for i, rule in enumerate(rules):
                        owner = default.get((direction, canon(rule)))
                        if owner:
                            findings.append((ns, f"{name} {direction} rule {i} is already in {owner}, which selects every pod: drop it here"))
                # 4. several toFQDNs rules with the same ports -> one list
                fq = [i for i, r in enumerate(rules) if "toFQDNs" in r]
                by_ports = collections.defaultdict(list)
                for i in fq:
                    by_ports[canon(rules[i].get("toPorts"))].append(i)
                for ports, idx in by_ports.items():
                    if len(idx) > 1:
                        findings.append((ns, f"{name} {direction}: toFQDNs rules {idx} share ports {ports} and could be one list"))
    # 5. the same cross-namespace peer admitted in many namespaces
    peers = collections.defaultdict(set)
    for path, pol in policies:
        for rule in pol["spec"].get("ingress") or []:
            for sel in rule.get("fromEndpoints") or []:
                ml = sel.get("matchLabels") or {}
                if NS_LABEL in ml:
                    peers[canon(ml)].add(pol["metadata"]["namespace"])
    for peer, nss in sorted(peers.items(), key=lambda kv: -len(kv[1])):
        if len(nss) >= 5:
            findings.append(("cluster", f"{peer} is admitted by {len(nss)} namespaces ({', '.join(sorted(nss))}); the same rule every time, so keep the wording identical, or hold it once in a CiliumClusterwideNetworkPolicy if the per-namespace file stops being the reader's entry point"))
    # 6. the same rule in most namespaces' whole-namespace policies: a
    #    cluster-wide policy could hold it once. Such a policy must carry
    #    enableDefaultDeny false in both directions, or every endpoint it
    #    selects, policy or not, flips into default-deny.
    recurring = collections.defaultdict(set)
    for path, pol in policies:
        if pol["spec"].get("endpointSelector") != {}:
            continue
        for direction in ("ingress", "egress"):
            for rule in pol["spec"].get(direction) or []:
                recurring[(direction, canon(rule))].add(pol["metadata"]["namespace"])
    threshold = max(5, (len(by_ns) * 3) // 5)
    globals_ = [(d, r, nss) for (d, r), nss in recurring.items() if len(nss) >= threshold]
    if globals_:
        print(f"## Cluster-wide candidates (rules in at least {threshold} of {len(by_ns)} namespaces)\n")
        print("A CiliumClusterwideNetworkPolicy with endpointSelector {} and\n"
              "enableDefaultDeny: {ingress: false, egress: false} adds the allow to every\n"
              "endpoint without putting endpoints that have no policy into default-deny.\n"
              "The per-namespace file then drops the rule, and its description loses a\n"
              "line the reader would otherwise find there.\n")
        for direction, rule, nss in sorted(globals_, key=lambda x: -len(x[2])):
            missing = sorted(set(by_ns) - nss)
            print(f"# {direction}, {len(nss)} namespaces" + (f"; not in {', '.join(missing)}" if missing else ""))
            print(yaml.safe_dump([json.loads(rule)], sort_keys=False).rstrip())
            print()
    if not findings:
        print("nothing to consolidate")
    for ns, text in findings:
        print(f"- [{ns}] {text}")
    print(f"\n{len(policies)} policies in {len(by_ns)} namespaces, {len(findings)} findings")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="print the AUDIT entries from Loki as JSON lines")
    f.add_argument("--since", default="7d", help="e.g. 7d, 12h, 30m")
    f.add_argument("--loki", default="http://localhost:13100")
    f.set_defaults(func=fetch)
    p = sub.add_parser("propose", help="rules for every audited flow, grouped by policy file")
    p.add_argument("file", help="JSON lines from fetch, or from hubble observe -o json")
    p.set_defaults(func=propose)
    c = sub.add_parser("consolidate", help="policies and rules that could be merged")
    c.set_defaults(func=consolidate)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
