#!/usr/bin/env bash
# Vendors the Mermaid runtime the documentation site serves itself into
# docs/assets/javascripts/, so the theme does not fetch it from unpkg.com.
#
# Renovate tracks the pin below; like the fonts, its PR only moves the pin -
# run this script on the branch and commit the files it writes:
#
#   make mermaid          # download the pinned release
#   make mermaid-check    # fail if the committed files are not that release
#
set -euo pipefail

# renovate: datasource=npm depName=mermaid packageName=mermaid versioning=semver
MERMAID_VERSION="12.3.0"

# "<path in archive>:<name written to disk>"
MERMAID_FILES=(
  "package/dist/mermaid.min.js:mermaid.min.js"
  "package/LICENSE:mermaid-LICENSE.txt"
)

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
js_dir="$repo_root/docs/assets/javascripts"

check_only=false
case "${1:-}" in
  --check) check_only=true ;;
  "") ;;
  *)
    echo "usage: ${0##*/} [--check]" >&2
    exit 2
    ;;
esac

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

echo "==> mermaid $MERMAID_VERSION"
curl --fail --silent --show-error --location --output "$work_dir/mermaid.tgz" \
  "https://registry.npmjs.org/mermaid/-/mermaid-$MERMAID_VERSION.tgz"

mkdir -p "$work_dir/staged"
for spec in "${MERMAID_FILES[@]}"; do
  src="${spec%%:*}"
  dst="${spec##*:}"
  tar -xzf "$work_dir/mermaid.tgz" -C "$work_dir" "$src"
  mv "$work_dir/$src" "$work_dir/staged/$dst"
done

if [ "$check_only" = true ]; then
  if diff --brief --recursive "$work_dir/staged" "$js_dir"; then
    echo "docs/assets/javascripts is in sync with the pinned release"
  else
    echo "docs/assets/javascripts does not match the pinned release; run 'make mermaid'" >&2
    exit 1
  fi
  exit 0
fi

mkdir -p "$js_dir"
rm -f "$js_dir"/mermaid*
cp -R "$work_dir/staged/." "$js_dir/"
echo "==> wrote ${#MERMAID_FILES[@]} files to docs/assets/javascripts"
