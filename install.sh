#!/bin/sh
# Install a tagged version of the shared agent workflows into a project.
#
#   sh install.sh <tag> [project-dir]
#
# Copies the docs into <project-dir>/.agent-workflows/ (with a VERSION file)
# and the skills into <project-dir>/.claude/skills/. Skills installed by an
# earlier run are removed first, so a skill dropped upstream doesn't linger.
# Skills the project wrote itself are left alone, unless one has the same
# name as a shared skill.
set -eu

tag="${1:?usage: install.sh <tag> [project-dir]}"
dest="${2:-.}"
repo="${AGENT_WORKFLOWS_REPO:-https://github.com/deepconcern/agent-workflows.git}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" "$repo" "$tmp/src"
src="$tmp/src"

docs="$dest/.agent-workflows"
skills="$dest/.claude/skills"

if [ -f "$docs/SKILLS" ]; then
	while IFS= read -r name; do
		[ -n "$name" ] && rm -rf "${skills:?}/$name"
	done <"$docs/SKILLS"
fi
rm -rf "$docs"
mkdir -p "$docs" "$skills"

cp -R "$src/README.md" "$src/CHANGELOG.md" "$src/principles.md" \
	"$src/roles" "$src/stacks" "$src/templates" "$src/workflows" "$docs/"

: >"$docs/SKILLS"
for dir in "$src"/skills/*/; do
	name="$(basename "$dir")"
	rm -rf "${skills:?}/$name"
	cp -R "$dir" "$skills/$name"
	echo "$name" >>"$docs/SKILLS"
done

echo "$tag" >"$docs/VERSION"
echo "Installed agent workflows $tag into $docs and $skills"
