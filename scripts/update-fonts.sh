#!/usr/bin/env bash
# Vendors the webfonts the documentation site serves itself (see
# docs/stylesheets/fonts.css) into docs/assets/fonts/.
#
# Renovate tracks the two pinned versions below and opens a PR when either
# upstream cuts a release. Because the fonts are binaries, that PR only moves
# the pin - run this script on the branch and commit the files it writes:
#
#   make fonts          # download the pinned releases into docs/assets/fonts
#   make fonts-check    # fail if the committed files are not those releases
#
set -euo pipefail

# renovate: datasource=github-releases depName=inter packageName=rsms/inter versioning=regex:^v(?<major>\d+)\.(?<minor>\d+)$
INTER_VERSION="v4.1"
# renovate: datasource=github-releases depName=jetbrains-mono packageName=JetBrains/JetBrainsMono versioning=regex:^v(?<major>\d+)\.(?<minor>\d+)$
JETBRAINS_MONO_VERSION="v2.304"

# Inter ships one variable file per style, covering the whole weight range.
# JetBrains Mono has no variable webfont upstream, so take the four static
# faces the theme asks for. "<path in archive>:<name written to disk>".
INTER_FILES=(
  "web/InterVariable.woff2:InterVariable.woff2"
  "web/InterVariable-Italic.woff2:InterVariable-Italic.woff2"
  "LICENSE.txt:Inter-LICENSE.txt"
)
JETBRAINS_MONO_FILES=(
  "fonts/webfonts/JetBrainsMono-Regular.woff2:JetBrainsMono-Regular.woff2"
  "fonts/webfonts/JetBrainsMono-Italic.woff2:JetBrainsMono-Italic.woff2"
  "fonts/webfonts/JetBrainsMono-Bold.woff2:JetBrainsMono-Bold.woff2"
  "fonts/webfonts/JetBrainsMono-BoldItalic.woff2:JetBrainsMono-BoldItalic.woff2"
  "OFL.txt:JetBrainsMono-LICENSE.txt"
)

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
font_dir="$repo_root/docs/assets/fonts"

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

# fetch <name> <version> <archive-url> <file spec>...
fetch() {
  local name="$1" version="$2" url="$3"
  shift 3

  echo "==> $name $version"
  curl --fail --silent --show-error --location --output "$work_dir/$name.zip" "$url"

  local spec src dst
  for spec in "$@"; do
    src="${spec%%:*}"
    dst="${spec##*:}"
    unzip -o -j -q "$work_dir/$name.zip" "$src" -d "$work_dir/$name"
    mv "$work_dir/$name/$(basename "$src")" "$work_dir/staged/$dst"
  done
}

mkdir -p "$work_dir/staged"

fetch inter "$INTER_VERSION" \
  "https://github.com/rsms/inter/releases/download/$INTER_VERSION/Inter-${INTER_VERSION#v}.zip" \
  "${INTER_FILES[@]}"

fetch jetbrains-mono "$JETBRAINS_MONO_VERSION" \
  "https://github.com/JetBrains/JetBrainsMono/releases/download/$JETBRAINS_MONO_VERSION/JetBrainsMono-${JETBRAINS_MONO_VERSION#v}.zip" \
  "${JETBRAINS_MONO_FILES[@]}"

if [ "$check_only" = true ]; then
  # diff catches drift in both directions: a stale file, and a file in
  # docs/assets/fonts/ that no longer comes from a pinned release.
  if diff --brief --recursive "$work_dir/staged" "$font_dir"; then
    echo "docs/assets/fonts is in sync with the pinned releases"
  else
    echo "docs/assets/fonts does not match the pinned releases; run 'make fonts'" >&2
    exit 1
  fi
  exit 0
fi

mkdir -p "$font_dir"
rm -f "$font_dir"/*.woff2 "$font_dir"/*-LICENSE.txt
cp -R "$work_dir/staged/." "$font_dir/"
echo "==> wrote $(ls -1 "$font_dir" | wc -l | tr -d ' ') files to docs/assets/fonts"
