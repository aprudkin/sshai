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
find "$dist_dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

source_date_epoch=$(git -C "$repo_dir" log -1 --format=%ct)
export SOURCE_DATE_EPOCH="$source_date_epoch" TZ=UTC

licenses="$stage_root/THIRD_PARTY_LICENSES"
(cd "$repo_dir" && python3 scripts/collect-third-party-licenses.py "$licenses")

while read -r goos goarch archive; do
  name="sshai_${version}_${goos}_${goarch}"
  stage="$stage_root/$name"
  mkdir -p -- "$stage/skills" "$stage/extensions"

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
  cp -R "$repo_dir/extensions/sshai-mode" "$stage/extensions/"
  cp -R "$licenses" "$stage/"

  python3 - "$stage" "$source_date_epoch" <<'PY'
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
epoch = int(sys.argv[2])
for path in sorted(root.rglob("*"), reverse=True):
    os.utime(path, (epoch, epoch), follow_symlinks=False)
os.utime(root, (epoch, epoch), follow_symlinks=False)
PY

  if [ "$archive" = zip ]; then
    (cd "$stage_root" && find "$name" -print | LC_ALL=C sort | zip -Xq "$dist_dir/$name.zip" -@)
  else
    python3 - "$stage_root" "$name" "$dist_dir/$name.tar.gz" "$source_date_epoch" <<'PY'
import gzip
import pathlib
import sys
import tarfile

stage_root = pathlib.Path(sys.argv[1])
name = sys.argv[2]
output = pathlib.Path(sys.argv[3])
epoch = int(sys.argv[4])
root = stage_root / name
paths = [root, *sorted(root.rglob("*"))]
with output.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive_file:
            for path in paths:
                info = archive_file.gettarinfo(str(path), arcname=path.relative_to(stage_root).as_posix())
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = epoch
                if info.isreg():
                    with path.open("rb") as source:
                        archive_file.addfile(info, source)
                else:
                    archive_file.addfile(info)
PY
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
