#!/usr/bin/env bash
# Prints the notes of a release: what changed in that version (from
# .github/releases/<tag>.md, when the file exists), then the install
# notes every release shares (.github/release-notes.md).
#
# Usage: scripts/release_notes.sh <tag> <owner> <owner/repo>
# Run from the repository root. {{VERSION}}, {{TAG}}, {{OWNER}} and
# {{REPO}} in both files are replaced.
set -eu

if [ "$#" -ne 3 ]; then
  echo "usage: release_notes.sh <tag> <owner> <owner/repo>" >&2
  exit 1
fi

tag="$1"
owner="$2"
repo="$3"

# The tag becomes part of a file name.
case "$tag" in
  v[0-9]*) ;;
  *) echo "Not a version tag: $tag" >&2; exit 1 ;;
esac
case "$tag" in
  *[!A-Za-z0-9._-]*) echo "Not a version tag: $tag" >&2; exit 1 ;;
esac

version="${tag#v}"
owner="$(echo "$owner" | tr '[:upper:]' '[:lower:]')"

fill() {
  sed -e "s|{{VERSION}}|$version|g" -e "s|{{TAG}}|$tag|g" \
      -e "s|{{OWNER}}|$owner|g" -e "s|{{REPO}}|$repo|g" "$1"
}

changes=".github/releases/$tag.md"

if [ -f "$changes" ]; then
  fill "$changes"
  echo
fi

fill .github/release-notes.md
