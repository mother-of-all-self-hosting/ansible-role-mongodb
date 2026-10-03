#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Slavi Pantaleev
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Fails unless the MongoDB release that `mongodb_version` points to (major.minor)
# is described in `mongodb_startable_fcvs_default`.
#
# Without an entry, the role cannot tell whether existing data can be started on
# by the new release, and would refuse to install it. Catching the omission here
# means that a Renovate pull request which bumps MongoDB to a new major or minor
# release fails its checks until somebody adds the entry.

set -euo pipefail

defaults_file="${1:-defaults/main.yml}"

version=$(sed -n -E 's/^mongodb_version: *"?([^" ]+)"? *$/\1/p' "$defaults_file")
release=$(printf '%s' "$version" | grep -o -E '^[0-9]+\.[0-9]+' || true)

if [ -z "$release" ]; then
    echo "Could not determine the MongoDB release from mongodb_version (\`$version\`) in $defaults_file" >&2
    exit 1
fi

# The entries are the indented lines that directly follow `mongodb_startable_fcvs_default:`.
entries=$(sed -n '/^mongodb_startable_fcvs_default:/,/^[^ ]/{/^  /p}' "$defaults_file")

if ! printf '%s\n' "$entries" | grep -q -E "^  \"${release//./\\.}\":"; then
    cat >&2 <<EOF
mongodb_version is $version, but MongoDB $release is not described in mongodb_startable_fcvs_default ($defaults_file).

Add an entry listing the feature compatibility versions that MongoDB $release can start on.
They are its own FCV, the FCV of the release before it, and the FCV of the last LTS release,
as listed in src/mongo/util/version/releases.yml in the MongoDB source for that release.
EOF
    exit 1
fi
