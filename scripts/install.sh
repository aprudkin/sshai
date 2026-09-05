#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH='' cd -- "$script_dir/.." && pwd)
requested_dir=${SSHAI_INSTALL_DIR:-"$HOME/.local/bin"}
requested_share_dir=${SSHAI_SHARE_DIR:-"$HOME/.local/share/sshai"}

if [ -z "$requested_dir" ] || [ "$requested_dir" = "/" ] || [ "$requested_dir" = "$HOME" ]; then
  printf '%s\n' "refusing unsafe install directory: $requested_dir" >&2
  exit 1
fi
if [ -z "$requested_share_dir" ] || [ "$requested_share_dir" = "/" ] || [ "$requested_share_dir" = "$HOME" ]; then
  printf '%s\n' "refusing unsafe share directory: $requested_share_dir" >&2
  exit 1
fi
if [ -L "$requested_dir" ] || { [ -e "$requested_dir" ] && [ ! -d "$requested_dir" ]; }; then
  printf '%s\n' "refusing unsafe install directory: $requested_dir" >&2
  exit 1
fi
if [ -L "$requested_share_dir" ] || { [ -e "$requested_share_dir" ] && [ ! -d "$requested_share_dir" ]; }; then
  printf '%s\n' "refusing unsafe share directory: $requested_share_dir" >&2
  exit 1
fi

mkdir -p -- "$requested_dir" "$requested_share_dir"
install_dir=$(CDPATH='' cd -- "$requested_dir" && pwd)
share_dir=$(CDPATH='' cd -- "$requested_share_dir" && pwd)
target_file="$install_dir/sshai"
skills_dir="$share_dir/skills"
skill_dir="$skills_dir/sshai"
skill_file="$skill_dir/SKILL.md"
source_skill="$repo_dir/skills/sshai/SKILL.md"

if [ -L "$target_file" ]; then
  printf '%s\n' "refusing to replace symlink: $target_file" >&2
  exit 1
fi
if [ -e "$target_file" ] && [ ! -f "$target_file" ]; then
  printf '%s\n' "refusing to replace non-file: $target_file" >&2
  exit 1
fi

if [ ! -f "$source_skill" ] || [ -L "$source_skill" ]; then
  printf '%s\n' "missing or unsafe bundled skill: $source_skill" >&2
  exit 1
fi
if [ -L "$skills_dir" ] || { [ -e "$skills_dir" ] && [ ! -d "$skills_dir" ]; }; then
  printf '%s\n' "refusing unsafe skills directory: $skills_dir" >&2
  exit 1
fi
mkdir -p -- "$skills_dir"
if [ -L "$skill_dir" ] || { [ -e "$skill_dir" ] && [ ! -d "$skill_dir" ]; }; then
  printf '%s\n' "refusing unsafe skill directory: $skill_dir" >&2
  exit 1
fi
mkdir -p -- "$skill_dir"
if [ -L "$skill_file" ] || { [ -e "$skill_file" ] && [ ! -f "$skill_file" ]; }; then
  printf '%s\n' "refusing to replace unsafe skill file: $skill_file" >&2
  exit 1
fi

temporary_file=$(mktemp "$install_dir/.sshai.install.XXXXXX")
temporary_skill=$(mktemp "$skill_dir/.SKILL.md.install.XXXXXX")
cleanup() {
  if [ -n "${temporary_file:-}" ] && [ -f "$temporary_file" ]; then
    rm -f -- "$temporary_file"
  fi
  if [ -n "${temporary_skill:-}" ] && [ -f "$temporary_skill" ]; then
    rm -f -- "$temporary_skill"
  fi
}
trap cleanup EXIT HUP INT TERM

(cd "$repo_dir" && go build -trimpath -o "$temporary_file" ./cmd/sshai)
cp -- "$source_skill" "$temporary_skill"
chmod 0755 "$temporary_file"
chmod 0644 "$temporary_skill"
mv -f -- "$temporary_skill" "$skill_file"
temporary_skill=
mv -f -- "$temporary_file" "$target_file"
temporary_file=

"$target_file" help >/dev/null
printf '%s\n' "installed sshai to $target_file"
printf '%s\n' "installed agent skill to $skill_dir"
