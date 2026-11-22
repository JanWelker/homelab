"""Turn a container's OCI seccomp section into a SeccompProfile with the log flag."""
import json
import sys
import textwrap

src = json.load(open(sys.argv[1]))
out = [
    "---",
    "# The runtime's default seccomp profile with the log flag: it refuses the same",
    "# syscalls and records each refusal. Generated, do not edit:",
    "# docs/platform/security-profiles-operator.md#seccomp",
    "apiVersion: security-profiles-operator.x-k8s.io/v1",
    "kind: SeccompProfile",
    "metadata:",
    "  name: runtime-default-log",
    "spec:",
    f"  defaultAction: {src['defaultAction']}",
    "  flags:",
    "    - SECCOMP_FILTER_FLAG_LOG",
    "  architectures:",
]
out += [f"    - {a}" for a in src["architectures"]]
out.append("  syscalls:")
for s in src["syscalls"]:
    out.append(f"    - action: {s['action']}")
    if s.get("errnoRet") is not None:
        out.append(f"      errnoRet: {s['errnoRet']}")
    if s.get("args"):
        out.append("      args:")
        for a in s["args"]:
            out.append(f"        - index: {a['index']}")
            out.append(f"          op: {a['op']}")
            out.append(f"          value: {a['value']}")
            if a.get("valueTwo"):
                out.append(f"          valueTwo: {a['valueTwo']}")
    names = ", ".join(s["names"])
    wrapped = textwrap.wrap(names, 68)
    if len(wrapped) == 1:
        out.append(f"      names: [{wrapped[0]}]")
    else:
        out.append("      names:")
        out.append("        [")
        out += [f"          {w}" for w in wrapped]
        out.append("        ]")
print("\n".join(out))
