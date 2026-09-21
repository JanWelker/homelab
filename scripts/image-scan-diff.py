#!/usr/bin/env python3
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
    return spec.get("sources") or [spec["source"]]


def render_chart(root, source, values_refs):
    """helm template for one chart source; returns the manifests as text."""
    helm = source.get("helm", {})
    args = ["helm", "template", "scan", source["chart"],
            "--repo", source["repoURL"], "--version", str(source["targetRevision"]),
            "--include-crds"]
    with tempfile.TemporaryDirectory() as workdir:
        for i, values_file in enumerate(helm.get("valueFiles", [])):
            for ref, ref_root in values_refs.items():
                values_file = values_file.replace(f"${ref}/", f"{ref_root}/")
            args += ["--values", values_file]
        if "valuesObject" in helm:
            path = pathlib.Path(workdir, "values.yaml")
            path.write_text(yaml.safe_dump(helm["valuesObject"]))
            args += ["--values", str(path)]
        # From an empty directory: helm prefers a local directory over the
        # repository when both carry the chart's name.
        result = subprocess.run(args, cwd=workdir, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"::warning title={source['chart']}::helm template failed: {result.stderr.strip()[-400:]}")
        return ""
    return result.stdout


def images_of(root, app_path):
    """Every image reference an Application deploys, from charts and files."""
    spec = yaml.safe_load(app_path.read_text())["spec"]
    text = []
    refs = {s["ref"]: str(root) for s in sources(spec) if "ref" in s}
    for source in sources(spec):
        if "chart" in source:
            text.append(render_chart(root, source, refs))
        elif "path" in source:
            directory = root / source["path"]
            exclude = source.get("directory", {}).get("exclude", "")
            for file in sorted(directory.glob("*.yaml")):
                if file.name != exclude:
                    text.append(file.read_text())
    return set(IMAGE_RE.findall("\n".join(text)))


def all_images(root, globs):
    found = set()
    for pattern in globs:
        for app in sorted(root.glob(pattern)):
            found |= images_of(root, app)
    return {i for i in found if "/" in i or ":" in i}


def repository(image):
    return image.split("@", 1)[0].rsplit(":", 1)[0] if ":" in image.split("/")[-1] else image.split("@", 1)[0]


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


def main(argv):
    base_root, head_root = pathlib.Path(argv[1]), pathlib.Path(argv[2])
    globs = tuple(argv[3:]) or DEFAULT_GLOBS
    base, head = all_images(base_root, globs), all_images(head_root, globs)
    changed = sorted(head - base)
    summary = [f"## Image scan\n", f"{len(head)} images referenced, {len(changed)} changed.\n"]
    if not changed:
        summary.append("Nothing to scan.\n")
    failed = False
    by_repo = {}
    for image in base:
        by_repo.setdefault(repository(image), image)
    for image in changed:
        new = fixable_criticals(image)
        old_image = by_repo.get(repository(image))
        old = fixable_criticals(old_image) if old_image else set()
        if new is None:
            summary.append(f"- `{image}`: scan failed, see the log\n")
            continue
        fresh = sorted(new - (old or set()))
        line = f"- `{image}`: {len(new)} fixable critical"
        line += f", {len(fresh)} new" if old_image else " (no previous image to compare)"
        summary.append(line + "\n")
        for cve, pkg in fresh:
            summary.append(f"    - {cve} in `{pkg}`\n")
        if fresh:
            failed = True
            print(f"::error title={image}::{len(fresh)} fixable critical(s) the previous image did not have")
    text = "".join(summary)
    print(text)
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
