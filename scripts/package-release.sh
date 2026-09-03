#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf '%s\n' "usage: $0 VERSION" >&2
  exit 2
fi

version=${1#v}
if ! printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  printf '%s\n' "invalid release version: $1" >&2
  exit 2
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
dist_dir="$repo_dir/dist"
stage_root=$(mktemp -d "${TMPDIR:-/tmp}/sshai-release.XXXXXX")

cleanup() {
  rm -rf -- "$stage_root"
}
trap cleanup EXIT HUP INT TERM

mkdir -p -- "$dist_dir"
find "$dist_dir" -maxdepth 1 -type f -name 'sshai_*' -delete
rm -f -- "$dist_dir/checksums.txt" "$dist_dir/sshai.rb"

licenses="$stage_root/THIRD_PARTY_LICENSES"
(cd "$repo_dir" && python3 scripts/collect-third-party-licenses.py "$licenses")

while read -r goos goarch archive; do
  name="sshai_${version}_${goos}_${goarch}"
  stage="$stage_root/$name"
  mkdir -p -- "$stage/skills"

  binary=sshai
  if [ "$goos" = windows ]; then
    binary=sshai.exe
  fi

  (
    cd "$repo_dir"
    CGO_ENABLED=0 GOOS="$goos" GOARCH="$goarch" \
      go build -trimpath -o "$stage/$binary" ./cmd/sshai
  )
  cp "$repo_dir/README.md" "$repo_dir/README.ru.md" "$repo_dir/LICENSE" "$stage/"
  cp -R "$repo_dir/skills/sshai" "$stage/skills/"
  cp -R "$licenses" "$stage/"

  if [ "$archive" = zip ]; then
    (cd "$stage_root" && zip -qr "$dist_dir/$name.zip" "$name")
  else
    tar -C "$stage_root" -czf "$dist_dir/$name.tar.gz" "$name"
  fi
done <<'PLATFORMS'
darwin amd64 tar.gz
darwin arm64 tar.gz
linux amd64 tar.gz
linux arm64 tar.gz
windows amd64 zip
windows arm64 zip
PLATFORMS

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$dist_dir" && sha256sum sshai_* > checksums.txt)
else
  (cd "$dist_dir" && shasum -a 256 sshai_* > checksums.txt)
fi

printf '%s\n' "release archives written to $dist_dir"
