---
name: renovate-audit
description: Check that every version pinned in this repository is tracked by Renovate, and fix the configuration when one is not. Use when the user says "why is this not updated by renovate", "check the renovate config for any other untracked pins", "before I merge check if this is tracked by renovate.json", "renovate still errors in both repos", "set up renovate to track the font releases", "ensure renovate catches all versions specified", or adds a new component, image or download. Covers walking the pins, the dry run that proves a manager sees them, the official-source rule for Flatcar components, and the pin-branch failure modes.
---

# renovate-audit

A misconfigured manager fails silently: the run succeeds and nothing is
checked. The rules and their reasons are in `docs/development/maintenance.md`
under Renovate; read that section before changing `renovate.json`, and put
any new rule there, not in a JSON comment.

## 1. Inventory the pins

Everything that names a version, tag, digest, release or URL with a version
in it:

```bash
grep -rnE 'targetRevision|(^|\s)tag:|image:|version:|sha256|releases/download|@v?[0-9]+\.[0-9]+' payload ansible/inventory.yaml scripts .github/workflows .pre-commit-config.yaml pyproject.toml zensical.toml 2>/dev/null | grep -vE '^\S+:\s*#'
```

Pair each hit with the manager that should own it: `argocd` for
`targetRevision`, `kubernetes` and `helm-values` for images in manifests and
values files, `github-actions`, `pre-commit`, `pep621` for Python, and the
custom regex managers for `ansible/inventory.yaml`, `scripts/update-fonts.sh`
and `# renovate:` annotations. The `Makefile` is deliberately untracked; it
reads `targetRevision` from the owning `application.yaml`.

An image tag inline in `helm.valuesObject:` is invisible to `helm-values`;
it needs a `# renovate:` annotation on the line above or a move into a
`values.yaml`.

## 2. Prove it with the dry run

```bash
LOG_LEVEL=debug npx --yes renovate@latest --platform=local --dry-run=lookup 2>&1 | tee /tmp/renovate.log
grep -n 'packageFiles with updates' -A 400 /tmp/renovate.log | grep -E '"depName"|"skipReason"|"updateType"|"currentValue"'
```

Every pin from §1 must appear with a `depName` and no `skipReason`. The
common skip reasons and their fixes are in the maintenance doc: a two-part
version needs a `versioningTemplate`, a bare Helm `tag:` goes in the
`pinDigests: false` rule, a full image reference in a custom manager needs
`autoReplaceStringTemplate`. Check that the currently pinned version passes
its own `allowedVersions` rule; a filter that excludes what is deployed
excludes everything.

## 3. Official sources only

The datasource is the project that owns the dependency in this repository,
not the OSS upstream. Flatcar itself comes from the release channel's
`version.txt`; Kubernetes, containerd and other sysext components from
`flatcar/sysext-bakery` releases with the prefix stripped; syslinux from
kernel.org. Never a mirror, fork or repackager. Ask before defaulting to an
upstream when the owner is unclear.

## 4. Read the Dependency Dashboard

`gh issue list --label dependencies` and open the dashboard issue. "Error
updating branch: update failure" on the shared pin branch means one
unwritable pin took every digest update with it. Updates parked behind
`minimumReleaseAge` are listed there too; that is expected and not a bug.

Both repositories share the policy: `homelab-apps` has its own
`renovate.json` and dashboard, so check both when the user says "both".

## 5. Ship

Configuration fixes in their own PR, separate from the version bump they
unblock, so the pin cannot go stale again. `renovate-validate.yaml` runs on
the PR; paste the dry-run lines for the affected dependency in the body.
`prometheus-operator-crds` merges before or with `kube-prometheus-stack`.
