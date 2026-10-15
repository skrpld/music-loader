#!/usr/bin/env bash
# Refuses a release requested from a manual workflow run (workflow_dispatch).
#
#   check-release.sh <tag prefix> <version>
#
# The release tag is <tag prefix><version> (e.g. cli-v2.10.0) and is created by
# publish-release.sh, so it must not exist yet. The run must be on main, the
# version must be MAJOR.MINOR.PATCH[-suffix] and newer than the latest tag of
# the same component.
set -euo pipefail

prefix="$1"
version="$2"
tag="${prefix}${version}"

fail() {
  echo "::error::$1"
  exit 1
}

# 0 when version $1 is newer than $2 (MAJOR.MINOR.PATCH[-suffix]; a version
# without a suffix is newer than the same version with one).
newer() {
  local a_core="${1%%-*}" b_core="${2%%-*}" a_pre="" b_pre="" i
  [[ "$1" == *-* ]] && a_pre="${1#*-}"
  [[ "$2" == *-* ]] && b_pre="${2#*-}"
  local -a a b
  IFS=. read -ra a <<< "$a_core"
  IFS=. read -ra b <<< "$b_core"
  for i in 0 1 2; do
    ((10#${a[i]} > 10#${b[i]})) && return 0
    ((10#${a[i]} < 10#${b[i]})) && return 1
  done
  [ "$a_pre" = "$b_pre" ] && return 1
  [ -z "$a_pre" ] && return 0
  [ -z "$b_pre" ] && return 1
  [ "$(printf '%s\n%s\n' "$a_pre" "$b_pre" | sort -V | tail -n 1)" = "$a_pre" ]
}

[ "$GITHUB_REF" = refs/heads/main ] \
  || fail "A release is published only from main, not from $GITHUB_REF. Run the workflow on main."

[ -n "$version" ] || fail "No release version given (the Android workflow needs the \"version\" input)."

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] \
  || fail "Version '$version' is not MAJOR.MINOR.PATCH[-suffix]."

tags=$(git ls-remote --tags --refs origin | sed -n "s|^.*refs/tags/${prefix}||p")

if grep -qxF -- "$version" <<< "$tags"; then
  fail "Tag $tag already exists. Bump the version, or delete the release and the tag to publish it again."
fi

latest=""
while read -r existing; do
  [[ "$existing" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || continue
  if [ -z "$latest" ] || newer "$existing" "$latest"; then
    latest="$existing"
  fi
done <<< "$tags"

if [ -n "$latest" ] && ! newer "$version" "$latest"; then
  fail "Version $version is not newer than the latest release tag ${prefix}${latest}."
fi

echo "Releasing $tag${latest:+ (previous: ${prefix}${latest})}."
