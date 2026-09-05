#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/sshai-install-test.XXXXXX")
cleanup() {
  rm -rf -- "$temporary_root"
}
trap cleanup EXIT HUP INT TERM

install_dir="$temporary_root/bin"
share_dir="$temporary_root/share"
run_install() {
  SSHAI_INSTALL_DIR="$install_dir" SSHAI_SHARE_DIR="$share_dir" "$repo_dir/scripts/install.sh" >/dev/null
}

mode() {
  if stat -c '%a' "$1" >/dev/null 2>&1; then
    stat -c '%a' "$1"
  else
    stat -f '%Lp' "$1"
  fi
}

run_install
binary="$install_dir/sshai"
skill="$share_dir/skills/sshai/SKILL.md"
cmp -s "$skill" "$repo_dir/skills/sshai/SKILL.md"
[ "$(mode "$binary")" = 755 ]
[ "$(mode "$skill")" = 644 ]
run_install

rm -- "$skill"
ln -s /dev/null "$skill"
if run_install >/dev/null 2>&1; then
  printf '%s\n' "installer accepted a skill symlink destination" >&2
  exit 1
fi
rm -- "$skill"
mkfifo "$skill"
if run_install >/dev/null 2>&1; then
  printf '%s\n' "installer accepted a non-regular skill destination" >&2
  exit 1
fi

linked_target="$temporary_root/linked-target"
mkdir -- "$linked_target"
install_link="$temporary_root/install-link"
ln -s "$linked_target" "$install_link"
if SSHAI_INSTALL_DIR="$install_link" SSHAI_SHARE_DIR="$temporary_root/share-for-install-link" \
  "$repo_dir/scripts/install.sh" >/dev/null 2>&1; then
  printf '%s\n' "installer accepted a symlinked install root" >&2
  exit 1
fi
share_link="$temporary_root/share-link"
ln -s "$linked_target" "$share_link"
if SSHAI_INSTALL_DIR="$temporary_root/bin-for-share-link" SSHAI_SHARE_DIR="$share_link" \
  "$repo_dir/scripts/install.sh" >/dev/null 2>&1; then
  printf '%s\n' "installer accepted a symlinked share root" >&2
  exit 1
fi

printf '%s\n' "installer behavior passed"
