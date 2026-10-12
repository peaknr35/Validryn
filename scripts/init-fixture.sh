#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "$0")/../fixtures/broken_calculator" && pwd)"
if git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Fixture is already a Git repository: $repo"
  exit 0
fi
git -C "$repo" init -q
git -C "$repo" config user.name "FTA Fixture"
git -C "$repo" config user.email "fta-fixture@example.invalid"
git -C "$repo" add .
git -C "$repo" commit -qm "Create broken calculator fixture"
echo "Initialized fixture: $repo"
