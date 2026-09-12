#!/bin/sh
set -eu

repo_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
manifest="$repo_dir/release/archive-files.txt"

python3 "$repo_dir/scripts/release_inventory.py" \
  validate-inputs "$repo_dir" "$manifest"
printf '%s\n' "release input policy passed"
