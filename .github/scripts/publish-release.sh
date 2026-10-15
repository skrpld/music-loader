#!/usr/bin/env bash
# Creates (or, on a re-run, updates) the GitHub release for a component tag.
#
#   publish-release.sh <tag> <title> <tag pattern> <file>...
#
# A pushed tag is used as is. A tag that does not exist yet (a release from a
# manual workflow run) is created on the checked-out commit.
#
# Release notes are generated from the changes since the previous tag of the
# same component (<tag pattern>, e.g. "cli-v*"), so CLI and Android releases
# do not list each other's history. A tag with a pre-release suffix
# (cli-v2.1.0-rc.1) is published as a pre-release.
set -euo pipefail

tag="$1"
title="$2"
pattern="$3"
shift 3

if gh release view "$tag" > /dev/null 2>&1; then
  echo "Release $tag exists, replacing its assets."
  gh release upload "$tag" "$@" --clobber
  exit 0
fi

args=(--title "$title" --generate-notes)
if git rev-parse -q --verify "refs/tags/$tag" > /dev/null; then
  args+=(--verify-tag)
  ref="$tag"
else
  ref=$(git rev-parse HEAD)
  args+=(--target "$ref")
  echo "Creating tag $tag on $ref."
fi
if previous=$(git describe --tags --abbrev=0 --match "$pattern" "${ref}^" 2> /dev/null); then
  args+=(--notes-start-tag "$previous")
fi
if [[ "$tag" =~ -v[0-9]+\.[0-9]+\.[0-9]+- ]]; then
  args+=(--prerelease)
fi
gh release create "$tag" "$@" "${args[@]}"
