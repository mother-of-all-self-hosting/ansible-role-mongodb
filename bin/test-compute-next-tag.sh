#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Slavi Pantaleev
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Exercises bin/compute-next-tag.sh against throwaway git repositories.
#
# Usage: bin/test-compute-next-tag.sh
#
# Every scenario creates a repository in a temporary directory, gives it role
# files and a release history, and then replays a series of merges through the
# real script, tagging as it goes just like the autotag workflow does. This
# repository is never touched and no network access is needed.

set -euo pipefail

script_under_test="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/compute-next-tag.sh"

failures=0
workdir=''

cleanup() {
	cd /
	if [ -n "$workdir" ]; then
		rm -rf "$workdir"
		workdir=''
	fi
}

trap cleanup EXIT

# Starts a scenario with a repository at MongoDB 8.0.12 which has already
# seen two releases of it (v8.0.12-0 and v8.0.12-1).
scenario() {
	echo "$1"

	cleanup
	workdir="$(mktemp -d)"

	mkdir -p "$workdir/bin" "$workdir/defaults" "$workdir/tasks" "$workdir/templates"
	cp "$script_under_test" "$workdir/bin/"
	cd "$workdir"

	git init -q -b main .
	git config user.email 'test@example.com'
	git config user.name 'Test'
	git config commit.gpgsign false

	# Mirrors defaults/main.yml: the Renovate annotation sits above the literal,
	# and the container image tag is derived from it rather than repeated.
	{
		printf '# renovate: datasource=docker depName=mongo versioning=semver\n'
		printf 'mongodb_version: 8.0.12\n'
		printf 'mongodb_container_image_tag: "{{ mongodb_version }}"\n'
	} > defaults/main.yml
	printf 'placeholder\n' > tasks/main.yml
	printf 'placeholder\n' > templates/env.j2
	printf 'placeholder\n' > README.md

	git add -A
	git commit -qm 'Initial commit'

	local release_number
	for release_number in 0 1; do
		git tag "v8.0.12-$release_number"
	done
}

# Applies a change, commits it, and tags whatever the script says it should be.
# Prints the tag, or nothing when the script decided against a release.
merge() {
	local change="$1" tag

	eval "$change"
	git add -A
	git commit -qm 'Merge'

	tag="$(bin/compute-next-tag.sh 2>/dev/null)"

	if [ -n "$tag" ]; then
		git tag "$tag"
	fi

	printf '%s' "$tag"
}

expect() {
	local description="$1" expected="$2" actual="$3"

	if [ "$actual" = "$expected" ]; then
		printf '  ok   | %s -> %s\n' "$description" "${actual:-no release}"
	else
		printf '  FAIL | %s -> expected %s, got %s\n' "$description" "${expected:-no release}" "${actual:-no release}"
		failures=$((failures + 1))
	fi
}

bump_patch="sed -i 's|mongodb_version: 8.0.12|mongodb_version: 8.0.13|' defaults/main.yml"
revert_patch="sed -i 's|mongodb_version: 8.0.13|mongodb_version: 8.0.12|' defaults/main.yml"
bump_series="sed -i 's|mongodb_version: 8.0.12|mongodb_version: 8.3.8|' defaults/main.yml"
edit_task="printf 'a task\n' >> tasks/main.yml"
edit_template="printf 'a line\n' >> templates/env.j2"
edit_readme="printf 'documentation\n' >> README.md"
edit_script="printf '# a comment\n' >> bin/compute-next-tag.sh"

# The two merge orders below apply the same updates and must each end up with
# every update released exactly once, whichever order they arrive in.

scenario 'A version bump merged before other role changes'
expect 'version bump' v8.0.13-0 "$(merge "$bump_patch")"
expect 'task edit'    v8.0.13-1 "$(merge "$edit_task")"
expect 'template'     v8.0.13-2 "$(merge "$edit_template")"

scenario 'A version bump merged after other role changes'
expect 'task edit'    v8.0.12-2 "$(merge "$edit_task")"
expect 'version bump' v8.0.13-0 "$(merge "$bump_patch")"

scenario 'Commits that do not affect the role'
expect 'README'   ''        "$(merge "$edit_readme")"
expect 'a script' ''        "$(merge "$edit_script")"
expect 'a task'   v8.0.12-2 "$(merge "$edit_task")"

# MongoDB jumps between release series (8.0.x -> 8.3.x) as well as within one,
# and a series jump is just another version the counter has not seen before.
scenario 'A jump to another MongoDB release series'
expect 'series bump' v8.3.8-0 "$(merge "$bump_series")"
expect 'task edit'   v8.3.8-1 "$(merge "$edit_task")"

scenario 'Release numbers past 9'
for release_number in 2 3 4 5 6 7 8 9 10; do
	git tag "v8.0.12-$release_number"
done
expect 'a task' v8.0.12-11 "$(merge "$edit_task")"

scenario 'Reverting to an already released version'
merge "$bump_patch" > /dev/null
# The role is now identical to what v8.0.12-1 already published, so there is
# nothing new to release.
expect 'a revert' ''        "$(merge "$revert_patch")"

scenario 'Reverting to an already released version, with a change'
merge "$bump_patch" > /dev/null
expect 'a revert' v8.0.12-2 "$(merge "$revert_patch && $edit_task")"

# The version must come from `mongodb_version`, the literal Renovate rewrites,
# and never from a variable that derives its value from it through Jinja.
# Ordering the file the other way around would let a loose pattern paired with
# `head -n1` pick up `{{ mongodb_version }}` and tag a template expression.
scenario 'A Jinja-derived variable is not mistaken for the version'
{
	printf 'mongodb_container_image_tag: "{{ mongodb_version }}"\n'
	printf '# renovate: datasource=docker depName=mongo versioning=semver\n'
	printf 'mongodb_version: 8.0.12\n'
} > defaults/main.yml
expect 'a task' v8.0.12-2 "$(merge "$edit_task")"

# A rename of the version variable must fail loudly rather than silently
# producing a tag with an empty or nonsensical version in it.
scenario 'The version variable has been renamed away'
printf 'mongodb_release: 8.0.12\n' > defaults/main.yml
git add -A
git commit -qm 'Rename'
if bin/compute-next-tag.sh >/dev/null 2>&1; then
	expect 'a rename' 'a failure' 'success'
else
	expect 'a rename' 'a failure' 'a failure'
fi

if [ "$failures" -gt 0 ]; then
	echo >&2 "$failures scenario(s) behaved unexpectedly"
	exit 1
fi

echo 'All scenarios behaved as expected'
