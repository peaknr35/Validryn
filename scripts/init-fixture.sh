#!/usr/bin/env bash
# Copies the tracked fixture into .fta/fixtures and makes the copy its own Git
# repository. The tracked files stay plain so the main repository never
# contains a nested .git directory.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
source="$root/fixtures/broken_calculator"
repo="$root/.fta/fixtures/broken_calculator"
if [ -d "$repo/.git" ]; then
  echo "Fixture repository already exists: $repo"
  exit 0
fi
mkdir -p "$(dirname "$repo")"
cp -R "$source" "$repo"
find "$repo" -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +
git -C "$repo" init -q
git -C "$repo" config user.name "FTA Fixture"
git -C "$repo" config user.email "fta-fixture@example.invalid"
git -C "$repo" add .
git -C "$repo" commit -qm "Create broken calculator fixture"
echo "Initialized fixture repository: $repo"
