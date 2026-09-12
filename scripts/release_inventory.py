#!/usr/bin/env python3
"""Validate and stage the files included in sshai binary archives."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import zipfile


REQUIRED_STATIC_FILES = frozenset(
    {
        "README.md",
        "README.ru.md",
        "LICENSE",
        "skills/sshai/SKILL.md",
    }
)
REQUIRED_LICENSE_FILES = frozenset(
    {
        "README.txt",
        "go/LICENSE",
        "go/PATENTS",
    }
)


class InventoryError(RuntimeError):
    """A release input or archive does not match the expected inventory."""


@dataclass(frozen=True)
class Entry:
    kind: str
    mode: int
    size: int = 0
    sha256: str = ""


def _safe_relative_path(value: str, *, label: str) -> PurePosixPath:
    if not value or value != value.strip():
        raise InventoryError(f"{label} is empty or has surrounding whitespace: {value!r}")
    if "\\" in value or "\x00" in value or any(ord(char) < 32 for char in value):
        raise InventoryError(f"{label} is unsafe: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise InventoryError(f"{label} is not a normalized relative path: {value!r}")
    if not path.parts or any(
        part in ("", ".", "..") or ":" in part for part in path.parts
    ):
        raise InventoryError(f"{label} is unsafe: {value!r}")
    return path


def _regular_file(path: Path, *, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise InventoryError(f"missing {label}: {path}") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise InventoryError(f"{label} must be a regular file, not a symlink: {path}")
    return details


def _directory(path: Path, *, label: str) -> os.stat_result:
    try:
        details = path.lstat()
    except FileNotFoundError as error:
        raise InventoryError(f"missing {label}: {path}") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise InventoryError(f"{label} must be a directory, not a symlink: {path}")
    return details


def _validate_source_file(repo: Path, relative: PurePosixPath) -> Path:
    current = repo
    for index, part in enumerate(relative.parts):
        current = current / part
        if index == len(relative.parts) - 1:
            _regular_file(current, label=f"release input {relative.as_posix()}")
        else:
            _directory(current, label=f"parent of release input {relative.as_posix()}")
    return current


def load_static_manifest(repo: Path, manifest: Path) -> tuple[PurePosixPath, ...]:
    """Load the static archive manifest and validate every source file."""
    repo = repo.resolve()
    _directory(repo, label="repository root")
    _regular_file(manifest, label="archive file manifest")
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise InventoryError(f"archive file manifest is not UTF-8: {manifest}") from error

    paths: list[PurePosixPath] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line or line.startswith("#"):
            continue
        relative = _safe_relative_path(line, label=f"manifest line {line_number}")
        value = relative.as_posix()
        if value in seen:
            raise InventoryError(f"duplicate manifest path: {value}")
        seen.add(value)
        _validate_source_file(repo, relative)
        paths.append(relative)

    missing = sorted(REQUIRED_STATIC_FILES - seen)
    if missing:
        raise InventoryError(
            "archive file manifest omits required static files: " + ", ".join(missing)
        )
    return tuple(paths)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_tree(root: Path) -> dict[str, Entry]:
    """Collect a regular-file/directory tree, rejecting links and special files."""
    root_details = _directory(root, label="inventory root")
    del root_details
    inventory: dict[str, Entry] = {}

    def walk_error(error: OSError) -> None:
        raise InventoryError(f"cannot inspect inventory tree {root}: {error}") from error

    for parent, directory_names, file_names in os.walk(
        root, topdown=True, onerror=walk_error, followlinks=False
    ):
        parent_path = Path(parent)
        for name in sorted(directory_names):
            path = parent_path / name
            details = path.lstat()
            relative = path.relative_to(root).as_posix()
            _safe_relative_path(relative, label="inventory path")
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise InventoryError(f"inventory directory must not be a link: {path}")
            inventory[relative] = Entry("directory", stat.S_IMODE(details.st_mode))
        for name in sorted(file_names):
            path = parent_path / name
            details = path.lstat()
            relative = path.relative_to(root).as_posix()
            _safe_relative_path(relative, label="inventory path")
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise InventoryError(f"inventory member must be a regular file: {path}")
            inventory[relative] = Entry(
                "file",
                stat.S_IMODE(details.st_mode),
                details.st_size,
                _digest(path),
            )
    return inventory


def validate_generated_licenses(root: Path) -> dict[str, Entry]:
    """Validate the collector output independently from static archive inputs."""
    inventory = collect_tree(root)
    files = {name for name, entry in inventory.items() if entry.kind == "file"}
    missing = sorted(REQUIRED_LICENSE_FILES - files)
    if missing:
        raise InventoryError(
            "generated THIRD_PARTY_LICENSES omits required files: " + ", ".join(missing)
        )
    return inventory


def validate_binary(path: Path) -> Entry:
    details = _regular_file(path, label="built executable")
    mode = stat.S_IMODE(details.st_mode)
    if mode & 0o111 == 0:
        raise InventoryError(f"built executable is not executable: {path}")
    return Entry("file", mode, details.st_size, _digest(path))


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, stat.S_IMODE(source.stat().st_mode))


def stage_release(
    repo: Path,
    manifest: Path,
    generated_licenses: Path,
    stage: Path,
    binary_name: str,
) -> dict[str, Entry]:
    """Copy only declared/generated payload into a stage containing one built binary."""
    binary_relative = _safe_relative_path(binary_name, label="binary name")
    if len(binary_relative.parts) != 1:
        raise InventoryError(f"binary name must not contain directories: {binary_name!r}")
    static_paths = load_static_manifest(repo, manifest)
    for relative in static_paths:
        if relative.parts[0] == "THIRD_PARTY_LICENSES" or relative.as_posix() == binary_name:
            raise InventoryError(
                f"static manifest path collides with generated payload: {relative.as_posix()}"
            )
    license_inventory = validate_generated_licenses(generated_licenses)
    _directory(stage, label="release staging directory")
    validate_binary(stage / binary_name)

    initial = collect_tree(stage)
    if set(initial) != {binary_name}:
        raise InventoryError(
            "release staging directory must initially contain only the built executable"
        )

    for relative in static_paths:
        _copy_file(repo / Path(*relative.parts), stage / Path(*relative.parts))

    license_destination = stage / "THIRD_PARTY_LICENSES"
    license_destination.mkdir(mode=0o755)
    for relative, entry in sorted(license_inventory.items()):
        source = generated_licenses / Path(*PurePosixPath(relative).parts)
        destination = license_destination / Path(*PurePosixPath(relative).parts)
        if entry.kind == "directory":
            destination.mkdir(parents=True, exist_ok=True)
            os.chmod(destination, entry.mode)
        else:
            _copy_file(source, destination)

    expected_names = {binary_name, "THIRD_PARTY_LICENSES"}
    for relative in static_paths:
        for parent in reversed(relative.parents):
            if parent != PurePosixPath("."):
                expected_names.add(parent.as_posix())
        expected_names.add(relative.as_posix())
    expected_names.update(
        f"THIRD_PARTY_LICENSES/{name}" for name in license_inventory
    )
    actual = collect_tree(stage)
    if set(actual) != expected_names:
        raise InventoryError(
            f"staged inventory differs: expected={sorted(expected_names)!r} "
            f"actual={sorted(actual)!r}"
        )
    validate_binary(stage / binary_name)
    return actual


def _archive_name(value: str, *, directory: bool) -> str:
    if directory and value.endswith("/"):
        value = value[:-1]
    elif not directory and value.endswith("/"):
        raise InventoryError(f"archive file name ends with '/': {value!r}")
    return _safe_relative_path(value, label="archive member").as_posix()


def _expected_archive(stage: Path, archive_root: str) -> dict[str, Entry]:
    root = _safe_relative_path(archive_root, label="archive root")
    if len(root.parts) != 1:
        raise InventoryError(f"archive root must be one path component: {archive_root!r}")
    root_details = _directory(stage, label="release staging directory")
    expected = {
        archive_root: Entry("directory", stat.S_IMODE(root_details.st_mode))
    }
    for name, entry in collect_tree(stage).items():
        expected[f"{archive_root}/{name}"] = entry
    return expected


def _compare_inventory(expected: dict[str, Entry], actual: dict[str, Entry]) -> None:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise InventoryError(
            f"archive inventory differs: missing={missing!r} extra={extra!r}"
        )
    for name in sorted(expected):
        if actual[name] != expected[name]:
            raise InventoryError(
                f"archive member differs for {name!r}: "
                f"expected={expected[name]!r} actual={actual[name]!r}"
            )


def _record_member(
    actual: dict[str, Entry], raw_name: str, entry: Entry, *, directory: bool
) -> None:
    name = _archive_name(raw_name, directory=directory)
    if name in actual:
        raise InventoryError(f"duplicate archive member: {name}")
    actual[name] = entry


def _validate_tar(path: Path) -> dict[str, Entry]:
    actual: dict[str, Entry] = {}
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.isdir():
                    entry = Entry("directory", member.mode & 0o777)
                    _record_member(actual, member.name, entry, directory=True)
                elif member.isreg():
                    source = archive.extractfile(member)
                    if source is None:
                        raise InventoryError(f"cannot read archive member: {member.name}")
                    digest = hashlib.sha256()
                    size = 0
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(chunk)
                        digest.update(chunk)
                    entry = Entry("file", member.mode & 0o777, size, digest.hexdigest())
                    _record_member(actual, member.name, entry, directory=False)
                else:
                    raise InventoryError(
                        f"archive links and special members are forbidden: {member.name!r}"
                    )
    except (tarfile.TarError, OSError) as error:
        raise InventoryError(f"cannot read tar archive {path}: {error}") from error
    return actual


def _validate_zip(path: Path) -> dict[str, Entry]:
    actual: dict[str, Entry] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                unix_mode = member.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)
                if member.is_dir():
                    if file_type not in (0, stat.S_IFDIR):
                        raise InventoryError(
                            f"archive links and special members are forbidden: {member.filename!r}"
                        )
                    entry = Entry("directory", stat.S_IMODE(unix_mode))
                    _record_member(actual, member.filename, entry, directory=True)
                else:
                    if file_type not in (0, stat.S_IFREG):
                        raise InventoryError(
                            f"archive links and special members are forbidden: {member.filename!r}"
                        )
                    digest = hashlib.sha256()
                    size = 0
                    with archive.open(member) as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            size += len(chunk)
                            digest.update(chunk)
                    entry = Entry(
                        "file", stat.S_IMODE(unix_mode), size, digest.hexdigest()
                    )
                    _record_member(actual, member.filename, entry, directory=False)
    except (zipfile.BadZipFile, OSError) as error:
        raise InventoryError(f"cannot read zip archive {path}: {error}") from error
    return actual


def validate_archive(path: Path, stage: Path, archive_root: str) -> None:
    """Validate exact names, types, modes, and contents in a tar.gz or zip archive."""
    expected = _expected_archive(stage, archive_root)
    _regular_file(path, label="release archive")
    if path.name.endswith(".tar.gz"):
        actual = _validate_tar(path)
    elif path.suffix == ".zip":
        actual = _validate_zip(path)
    else:
        raise InventoryError(f"unsupported archive format: {path}")
    _compare_inventory(expected, actual)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inputs = subparsers.add_parser("validate-inputs")
    inputs.add_argument("repo", type=Path)
    inputs.add_argument("manifest", type=Path)

    licenses = subparsers.add_parser("validate-licenses")
    licenses.add_argument("directory", type=Path)

    stage = subparsers.add_parser("stage-release")
    stage.add_argument("repo", type=Path)
    stage.add_argument("manifest", type=Path)
    stage.add_argument("licenses", type=Path)
    stage.add_argument("stage", type=Path)
    stage.add_argument("binary_name")

    archive = subparsers.add_parser("validate-archive")
    archive.add_argument("archive", type=Path)
    archive.add_argument("stage", type=Path)
    archive.add_argument("archive_root")

    arguments = parser.parse_args()
    try:
        if arguments.command == "validate-inputs":
            paths = load_static_manifest(arguments.repo, arguments.manifest)
            print(f"validated {len(paths)} static release inputs")
        elif arguments.command == "validate-licenses":
            inventory = validate_generated_licenses(arguments.directory)
            print(f"validated {len(inventory)} generated license entries")
        elif arguments.command == "stage-release":
            inventory = stage_release(
                arguments.repo,
                arguments.manifest,
                arguments.licenses,
                arguments.stage,
                arguments.binary_name,
            )
            print(f"staged {len(inventory)} release entries")
        else:
            validate_archive(arguments.archive, arguments.stage, arguments.archive_root)
            print(f"validated archive {arguments.archive}")
    except InventoryError as error:
        parser.exit(1, f"release inventory error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
