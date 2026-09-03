#!/bin/sh
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  printf '%s\n' "usage: $0 VERSION SHA256 [OUTPUT]" >&2
  exit 2
fi

version=${1#v}
sha256=$2
output=${3:-dist/sshai.rb}

if ! printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  printf '%s\n' "invalid release version: $1" >&2
  exit 2
fi
if ! printf '%s\n' "$sha256" | grep -Eq '^[0-9a-f]{64}$'; then
  printf '%s\n' "invalid SHA-256: $sha256" >&2
  exit 2
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
template="$repo_dir/release/homebrew/sshai.rb.tmpl"

mkdir -p -- "$(dirname -- "$output")"
sed -e "s/@VERSION@/$version/g" -e "s/@SHA256@/$sha256/g" "$template" > "$output"
printf '%s\n' "rendered $output"
