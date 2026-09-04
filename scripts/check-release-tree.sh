#!/bin/sh
set -eu

repo_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"

actual=$(mktemp)
expected=$(mktemp)
cleanup() {
  rm -f -- "$actual" "$expected"
}
trap cleanup EXIT HUP INT TERM

git ls-files --cached --others --exclude-standard |
  while IFS= read -r path; do
    if [ -e "$path" ]; then
      printf '%s\n' "$path"
    fi
  done |
  LC_ALL=C sort -u > "$actual"
LC_ALL=C sort -u release/source-allowlist.txt > "$expected"

if ! diff -u "$expected" "$actual"; then
  printf '%s\n' "release tree differs from release/source-allowlist.txt" >&2
  exit 1
fi

printf '%s\n' "release tree policy passed"
