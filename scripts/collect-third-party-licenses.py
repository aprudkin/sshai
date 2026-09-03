#!/usr/bin/env python3
"""Collect license notices for code linked into the sshai binary."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

LICENSE_PREFIXES = ("license", "copying", "notice")


def decode_json_stream(raw: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    values: list[dict[str, object]] = []
    offset = 0
    while offset < len(raw):
        while offset < len(raw) and raw[offset].isspace():
            offset += 1
        if offset == len(raw):
            break
        value, offset = decoder.raw_decode(raw, offset)
        values.append(value)
    return values


def license_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink()
        and path.name.lower().startswith(LICENSE_PREFIXES)
    )


def safe_name(module: str, version: str) -> str:
    value = f"{module}@{version}".replace("/", "__")
    return value.replace("\\", "__")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} OUTPUT_DIR", file=sys.stderr)
        return 2

    output = Path(sys.argv[1])
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    raw = subprocess.check_output(
        ["go", "list", "-deps", "-json", "./cmd/sshai"], text=True
    )
    modules: dict[str, tuple[str, Path]] = {}
    for package in decode_json_stream(raw):
        module = package.get("Module")
        if not isinstance(module, dict) or module.get("Main"):
            continue
        path = module.get("Path")
        version = module.get("Version")
        directory = module.get("Dir")
        if all(isinstance(value, str) and value for value in (path, version, directory)):
            modules[path] = (version, Path(directory))

    manifest = ["Third-party licenses bundled with sshai", ""]
    for module, (version, directory) in sorted(modules.items()):
        files = license_files(directory)
        if not files:
            raise RuntimeError(f"no license file found for {module}@{version}")
        destination = output / safe_name(module, version)
        destination.mkdir()
        for source in files:
            shutil.copyfile(source, destination / source.name)
        manifest.append(f"{module}@{version}: {', '.join(path.name for path in files)}")

    repository = Path(__file__).resolve().parent.parent
    go_destination = output / "go"
    go_destination.mkdir()
    go_files = {
        repository / "release/licenses/go-LICENSE": "LICENSE",
        repository / "release/licenses/go-PATENTS": "PATENTS",
    }
    for source, destination_name in go_files.items():
        if not source.is_file() or source.is_symlink():
            raise RuntimeError(f"missing Go notice file: {source}")
        shutil.copyfile(source, go_destination / destination_name)
    manifest.append("Go toolchain/runtime: LICENSE, PATENTS")

    (output / "README.txt").write_text("\n".join(manifest) + "\n")
    print(f"collected notices for {len(modules)} modules and the Go runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
