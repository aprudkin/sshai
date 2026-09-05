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
source_skills_dir="$repo_dir/skills"
source_skill_dir="$source_skills_dir/sshai"
source_skill="$source_skill_dir/SKILL.md"
extensions_dir="$share_dir/extensions"
extension_dir="$extensions_dir/sshai-mode"
extension_file="$extension_dir/index.ts"
source_extensions_dir="$repo_dir/extensions"
source_extension_dir="$source_extensions_dir/sshai-mode"
source_extension="$source_extension_dir/index.ts"

if [ -L "$target_file" ]; then
  printf '%s\n' "refusing to replace symlink: $target_file" >&2
  exit 1
fi
if [ -e "$target_file" ] && [ ! -f "$target_file" ]; then
  printf '%s\n' "refusing to replace non-file: $target_file" >&2
  exit 1
fi

if [ -L "$source_skills_dir" ] || [ ! -d "$source_skills_dir" ] || [ -L "$source_skill_dir" ] || [ ! -d "$source_skill_dir" ] || [ ! -f "$source_skill" ] || [ -L "$source_skill" ]; then
  printf '%s\n' "missing or unsafe bundled skill: $source_skill" >&2
  exit 1
fi
if [ -L "$source_extensions_dir" ] || [ ! -d "$source_extensions_dir" ] || [ -L "$source_extension_dir" ] || [ ! -d "$source_extension_dir" ] || [ ! -f "$source_extension" ] || [ -L "$source_extension" ]; then
  printf '%s\n' "missing or unsafe bundled extension: $source_extension" >&2
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
if [ -L "$extensions_dir" ] || { [ -e "$extensions_dir" ] && [ ! -d "$extensions_dir" ]; }; then
  printf '%s\n' "refusing unsafe extensions directory: $extensions_dir" >&2
  exit 1
fi
mkdir -p -- "$extensions_dir"
if [ -L "$extension_dir" ] || { [ -e "$extension_dir" ] && [ ! -d "$extension_dir" ]; }; then
  printf '%s\n' "refusing unsafe extension directory: $extension_dir" >&2
  exit 1
fi
mkdir -p -- "$extension_dir"
if [ -L "$extension_file" ] || { [ -e "$extension_file" ] && [ ! -f "$extension_file" ]; }; then
  printf '%s\n' "refusing to replace unsafe extension file: $extension_file" >&2
  exit 1
fi

temporary_file=$(mktemp "$install_dir/.sshai.install.XXXXXX")
temporary_skill=$(mktemp "$skill_dir/.SKILL.md.install.XXXXXX")
temporary_extension=$(mktemp "$extension_dir/.index.ts.install.XXXXXX")
cleanup() {
  if [ -n "${temporary_file:-}" ] && [ -f "$temporary_file" ]; then
    rm -f -- "$temporary_file"
  fi
  if [ -n "${temporary_skill:-}" ] && [ -f "$temporary_skill" ]; then
    rm -f -- "$temporary_skill"
  fi
  if [ -n "${temporary_extension:-}" ] && [ -f "$temporary_extension" ]; then
    rm -f -- "$temporary_extension"
  fi
}
trap cleanup EXIT HUP INT TERM

(cd "$repo_dir" && go build -trimpath -o "$temporary_file" ./cmd/sshai)
cp -- "$source_skill" "$temporary_skill"
cp -- "$source_extension" "$temporary_extension"
chmod 0755 "$temporary_file"
chmod 0644 "$temporary_skill" "$temporary_extension"
mv -f -- "$temporary_skill" "$skill_file"
temporary_skill=
mv -f -- "$temporary_extension" "$extension_file"
temporary_extension=
mv -f -- "$temporary_file" "$target_file"
temporary_file=

"$target_file" help >/dev/null
printf '%s\n' "installed sshai to $target_file"
printf '%s\n' "installed agent skill to $skill_dir"
printf '%s\n' "installed Pi extension to $extension_dir"
