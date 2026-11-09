#!/bin/bash
# Shared scaffold: a small repository on the Ratchet method. Called by each case's scaffold.sh
# with the case directory; copies _common, then the case's repo/, commits it on main, then
# commits the case's branch/ overlay (if any) on the branch named in branch/.name.
set -euo pipefail
case_dir="$1"
common_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp -R "$common_dir/docs" "$common_dir/justfile" .
cp -R "$case_dir/repo/." .

git init -q -b main
git add -A
git -c user.name=Sam -c user.email=sam@example.com commit -qm "Initial import"

if [ -d "$case_dir/branch" ]; then
    git checkout -q -b "$(cat "$case_dir/branch.name")"
    cp -R "$case_dir/branch/." .
    git add -A
    git -c user.name=Sam -c user.email=sam@example.com commit -qm "$(cat "$case_dir/branch.message")"
fi
