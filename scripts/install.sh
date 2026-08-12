#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
requested_dir=${SSHAI_INSTALL_DIR:-"$HOME/.local/bin"}

if [ -z "$requested_dir" ] || [ "$requested_dir" = "/" ] || [ "$requested_dir" = "$HOME" ]; then
  printf '%s\n' "refusing unsafe install directory: $requested_dir" >&2
  exit 1
fi

mkdir -p -- "$requested_dir"
install_dir=$(CDPATH='' cd -- "$requested_dir" && pwd)
target_file="$install_dir/sshai"

if [ -L "$target_file" ]; then
  printf '%s\n' "refusing to replace symlink: $target_file" >&2
  exit 1
fi
if [ -e "$target_file" ] && [ ! -f "$target_file" ]; then
  printf '%s\n' "refusing to replace non-file: $target_file" >&2
  exit 1
fi

temporary_file=$(mktemp "$install_dir/.sshai.install.XXXXXX")
cleanup() {
  if [ -n "${temporary_file:-}" ] && [ -f "$temporary_file" ]; then
    rm -f -- "$temporary_file"
  fi
}
trap cleanup EXIT HUP INT TERM

(cd "$repo_dir" && go build -trimpath -o "$temporary_file" ./cmd/sshai)
chmod 0755 "$temporary_file"
mv -f -- "$temporary_file" "$target_file"
temporary_file=

"$target_file" help >/dev/null
printf '%s\n' "installed sshai to $target_file"
