#!/usr/bin/env python3
# pylint: disable=invalid-name  # the script is named like its workflow, image-scan
"""Fail a pull request that introduces a fixable critical vulnerability.

Renders every ArgoCD Application under two checkouts, collects the container
images each one references, and scans with Trivy only the images the pull
request changes. A changed image fails the check when it carries a fixable
CRITICAL that the image it replaces did not: an existing finding that is
waiting on a release is not a reason to block the bump that gets closer to
it, and a new one is exactly when a person should look.

    image-scan-diff.py <base-checkout> <head-checkout> [<glob> ...]

The globs select application manifests relative to each checkout and default
to the platform layout. Requires helm and trivy on PATH.
"""

import fnmatch
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml

IMAGE_RE = re.compile(r"""^\s*(?:image|imageName):\s*["']?([^\s"'#]+)""", re.MULTILINE)
DEFAULT_GLOBS = ("payload/platform/*/application.yaml", "payload/argocd/application.yaml")


def sources(spec):
    """The Application's source list, whether it is single- or multi-source."""
    return spec.get("sources") or [spec["source"]]


def excluded(name, pattern):
    """Whether an ArgoCD directory.exclude glob matches a file name.

    ArgoCD uses Go's glob syntax, which fnmatch covers except for the brace
    group `{a,b}` -- expanded here into one fnmatch per alternative.
    """
    group = re.search(r"\{([^}]*)\}", pattern)
    if not group:
        return fnmatch.fnmatch(name, pattern)
    return any(excluded(name, pattern[:group.start()] + alt + pattern[group.end():])
               for alt in group.group(1).split(","))


def render_chart(source, values_refs):
    """helm template for one chart source; returns the manifests as text."""
    helm = source.get("helm", {})
    args = ["helm", "template", "scan", source["chart"],
            "--repo", source["repoURL"], "--version", str(source["targetRevision"]),
            "--include-crds"]
    with tempfile.TemporaryDirectory() as workdir:
        for values_file in helm.get("valueFiles", []):
            for ref, ref_root in values_refs.items():
                values_file = values_file.replace(f"${ref}/", f"{ref_root}/")
            args += ["--values", values_file]
        if "valuesObject" in helm:
            path = pathlib.Path(workdir, "values.yaml")
            path.write_text(yaml.safe_dump(helm["valuesObject"]), encoding="utf-8")
            args += ["--values", str(path)]
        # From an empty directory: helm prefers a local directory over the
        # repository when both carry the chart's name. That directory is not
        # the checkout, so every $values path has to be absolute.
        result = subprocess.run(args, cwd=workdir, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"::warning title={source['chart']}::helm template failed: "
              f"{result.stderr.strip()[-400:]}")
        return ""
    return result.stdout


def images_of(root, app_path):
    """Every image reference an Application deploys, from charts and files."""
    spec = yaml.safe_load(app_path.read_text(encoding="utf-8"))["spec"]
    text = []
    refs = {s["ref"]: str(root.resolve()) for s in sources(spec) if "ref" in s}
    for source in sources(spec):
        if "chart" in source:
            text.append(render_chart(source, refs))
        elif "path" in source:
            directory = root / source["path"]
            exclude = source.get("directory", {}).get("exclude", "")
            for file in sorted(directory.glob("*.yaml")):
                if not excluded(file.name, exclude):
                    text.append(file.read_text(encoding="utf-8"))
    return set(IMAGE_RE.findall("\n".join(text)))


def all_images(root, globs):
    """Every image referenced by the Applications the globs select under root."""
    found = set()
    for pattern in globs:
        for app in sorted(root.glob(pattern)):
            found |= images_of(root, app)
    return {i for i in found if "/" in i or ":" in i}


def repository(image):
    """The image reference without its tag and digest."""
    name = image.split("@", 1)[0]
    return name.rsplit(":", 1)[0] if ":" in name.split("/")[-1] else name


def fixable_criticals(image):
    """Set of (CVE, package) pairs with a fix, CRITICAL only."""
    result = subprocess.run(
        ["trivy", "image", "--quiet", "--severity", "CRITICAL", "--ignore-unfixed",
         "--scanners", "vuln", "--format", "json", image],
        capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"::warning title={image}::trivy failed: {result.stderr.strip()[-400:]}")
        return None
    report = json.loads(result.stdout or "{}")
    return {(v["VulnerabilityID"], v["PkgName"])
            for r in report.get("Results") or [] for v in r.get("Vulnerabilities") or []}


def scan(image, old_image):
    """Summary lines for one changed image; True when it carries a new critical."""
    new = fixable_criticals(image)
    if new is None:
        return [f"- `{image}`: scan failed, see the log\n"], False
    old = (fixable_criticals(old_image) if old_image else None) or set()
    fresh = sorted(new - old)
    line = f"- `{image}`: {len(new)} fixable critical"
    line += f", {len(fresh)} new" if old_image else " (no previous image to compare)"
    lines = [line + "\n"] + [f"    - {cve} in `{pkg}`\n" for cve, pkg in fresh]
    if fresh:
        print(f"::error title={image}::{len(fresh)} fixable critical(s) "
              "the previous image did not have")
    return lines, bool(fresh)


def main(argv):
    """Compare the two checkouts, scan what changed, write the job summary."""
    base_root, head_root = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    globs = tuple(argv[3:]) or DEFAULT_GLOBS
    base, head = all_images(base_root, globs), all_images(head_root, globs)
    changed = sorted(head - base)
    summary = ["## Image scan\n", f"{len(head)} images referenced, {len(changed)} changed.\n"]
    if not changed:
        summary.append("Nothing to scan.\n")
    by_repo = {}
    for image in base:
        by_repo.setdefault(repository(image), image)
    failed = False
    for image in changed:
        lines, fresh = scan(image, by_repo.get(repository(image)))
        summary += lines
        failed = failed or fresh
    text = "".join(summary)
    print(text)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
