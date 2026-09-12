from __future__ import annotations

from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
import warnings
import zipfile

from release_inventory import (
    InventoryError,
    REQUIRED_STATIC_FILES,
    load_static_manifest,
    stage_release,
    validate_archive,
    validate_generated_licenses,
)


class ReleaseInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        for relative in REQUIRED_STATIC_FILES:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"payload for {relative}\n", encoding="utf-8")
        self.manifest = self.repo / "release" / "archive-files.txt"
        self.manifest.parent.mkdir()
        self.manifest.write_text(
            "README.md\nREADME.ru.md\nLICENSE\nskills/sshai/SKILL.md\n",
            encoding="utf-8",
        )
        self.licenses = self.root / "generated-licenses"
        (self.licenses / "go").mkdir(parents=True)
        (self.licenses / "README.txt").write_text("inventory\n", encoding="utf-8")
        (self.licenses / "go" / "LICENSE").write_text("license\n", encoding="utf-8")
        (self.licenses / "go" / "PATENTS").write_text("patents\n", encoding="utf-8")

    def make_stage(self) -> Path:
        stage = self.root / "bundle"
        stage.mkdir()
        binary = stage / "sshai"
        binary.write_bytes(b"synthetic executable\n")
        binary.chmod(0o755)
        stage_release(self.repo, self.manifest, self.licenses, stage, "sshai")
        return stage

    def archive_members(self, stage: Path) -> list[tuple[Path, str]]:
        root_name = stage.name
        return [
            (stage, root_name),
            *[
                (path, f"{root_name}/{path.relative_to(stage).as_posix()}")
                for path in sorted(stage.rglob("*"))
            ],
        ]

    def make_archive(
        self,
        stage: Path,
        kind: str,
        *,
        omit: str | None = None,
        extra: bool = False,
        binary_mode: int | None = None,
    ) -> Path:
        suffix = ".tar.gz" if kind == "tar" else ".zip"
        output = self.root / f"candidate-{kind}{suffix}"
        members = self.archive_members(stage)
        if kind == "tar":
            with tarfile.open(output, "w:gz") as archive:
                for path, name in members:
                    if name == omit:
                        continue
                    info = archive.gettarinfo(path, arcname=name)
                    if binary_mode is not None and name == f"{stage.name}/sshai":
                        info.mode = binary_mode
                    if info.isreg():
                        with path.open("rb") as source:
                            archive.addfile(info, source)
                    else:
                        archive.addfile(info)
                if extra:
                    info = tarfile.TarInfo(f"{stage.name}/unexpected.txt")
                    info.mode = 0o644
                    info.size = 1
                    import io

                    archive.addfile(info, io.BytesIO(b"x"))
        else:
            with zipfile.ZipFile(output, "w") as archive:
                for path, name in members:
                    if name == omit:
                        continue
                    archive_name = name + "/" if path.is_dir() else name
                    info = zipfile.ZipInfo.from_file(path, arcname=archive_name)
                    if binary_mode is not None and name == f"{stage.name}/sshai":
                        info.external_attr = (
                            stat.S_IFREG | binary_mode
                        ) << 16
                    if path.is_dir():
                        archive.writestr(info, b"")
                    else:
                        archive.writestr(info, path.read_bytes())
                if extra:
                    info = zipfile.ZipInfo(f"{stage.name}/unexpected.txt")
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(info, b"x")
        return output

    def test_unrelated_repository_files_do_not_affect_static_inventory(self) -> None:
        (self.repo / "notes.local").write_text("unrelated\n", encoding="utf-8")
        paths = load_static_manifest(self.repo, self.manifest)
        self.assertEqual(set(REQUIRED_STATIC_FILES), {path.as_posix() for path in paths})

        stage = self.make_stage()
        self.assertFalse((stage / "notes.local").exists())

    def test_package_script_validates_before_clearing_dist(self) -> None:
        scripts = self.repo / "scripts"
        scripts.mkdir()
        source_scripts = Path(__file__).resolve().parent
        shutil.copyfile(source_scripts / "package-release.sh", scripts / "package-release.sh")
        shutil.copyfile(source_scripts / "release_inventory.py", scripts / "release_inventory.py")
        self.manifest.write_text("README.md\n", encoding="utf-8")
        dist = self.repo / "dist"
        dist.mkdir()
        sentinel = dist / "existing-candidate"
        sentinel.write_text("keep\n", encoding="utf-8")

        result = subprocess.run(
            ["sh", str(scripts / "package-release.sh"), "v1.2.3"],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(sentinel.is_file(), result.stderr)

    def test_required_static_member_cannot_be_removed_from_manifest(self) -> None:
        self.manifest.write_text(
            "README.md\nREADME.ru.md\nLICENSE\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(InventoryError, "required static files"):
            load_static_manifest(self.repo, self.manifest)

    def test_missing_unsafe_duplicate_and_symlinked_inputs_fail(self) -> None:
        cases = {
            "missing": "missing.txt",
            "traversal": "../outside.txt",
            "absolute": "/tmp/outside.txt",
            "dot": ".",
            "unnormalized": "skills//sshai/SKILL.md",
            "backslash": r"skills\\sshai\\SKILL.md",
            "duplicate": "README.md",
        }
        original = self.manifest.read_text(encoding="utf-8")
        for label, line in cases.items():
            with self.subTest(label=label):
                self.manifest.write_text(original + line + "\n", encoding="utf-8")
                with self.assertRaises(InventoryError):
                    load_static_manifest(self.repo, self.manifest)
        self.manifest.write_text(original, encoding="utf-8")

        target = self.repo / "target.txt"
        target.write_text("target\n", encoding="utf-8")
        link = self.repo / "linked.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        self.manifest.write_text(original + "linked.txt\n", encoding="utf-8")
        with self.assertRaisesRegex(InventoryError, "regular file"):
            load_static_manifest(self.repo, self.manifest)

    def test_static_manifest_cannot_replace_generated_payload(self) -> None:
        (self.repo / "sshai").write_text("not the built binary\n", encoding="utf-8")
        with self.manifest.open("a", encoding="utf-8") as manifest:
            manifest.write("sshai\n")
        stage = self.root / "bundle"
        stage.mkdir()
        binary = stage / "sshai"
        binary.write_text("built binary\n", encoding="utf-8")
        binary.chmod(0o755)
        with self.assertRaisesRegex(InventoryError, "collides"):
            stage_release(self.repo, self.manifest, self.licenses, stage, "sshai")

    def test_generated_license_tree_rejects_symlinks(self) -> None:
        link = self.licenses / "linked"
        try:
            link.symlink_to(self.licenses / "README.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaisesRegex(InventoryError, "regular file"):
            validate_generated_licenses(self.licenses)

    def test_exact_tar_and_zip_inventories_pass(self) -> None:
        stage = self.make_stage()
        for kind in ("tar", "zip"):
            with self.subTest(kind=kind):
                archive = self.make_archive(stage, kind)
                validate_archive(archive, stage, stage.name)

    def test_extra_and_missing_archive_members_fail_for_tar_and_zip(self) -> None:
        stage = self.make_stage()
        missing = f"{stage.name}/README.md"
        for kind in ("tar", "zip"):
            with self.subTest(kind=kind, mutation="missing"):
                archive = self.make_archive(stage, kind, omit=missing)
                with self.assertRaisesRegex(InventoryError, "missing"):
                    validate_archive(archive, stage, stage.name)
            with self.subTest(kind=kind, mutation="extra"):
                archive = self.make_archive(stage, kind, extra=True)
                with self.assertRaisesRegex(InventoryError, "extra"):
                    validate_archive(archive, stage, stage.name)

    def test_archives_preserve_executable_mode(self) -> None:
        stage = self.make_stage()
        self.assertEqual((stage / "sshai").stat().st_mode & 0o777, 0o755)
        for kind in ("tar", "zip"):
            with self.subTest(kind=kind, mode="preserved"):
                validate_archive(self.make_archive(stage, kind), stage, stage.name)
            with self.subTest(kind=kind, mode="removed"):
                archive = self.make_archive(stage, kind, binary_mode=0o644)
                with self.assertRaisesRegex(InventoryError, "sshai"):
                    validate_archive(archive, stage, stage.name)

    def test_archive_traversal_duplicate_and_links_fail(self) -> None:
        stage = self.make_stage()
        tar_path = self.root / "unsafe.tar.gz"
        with tarfile.open(tar_path, "w:gz") as archive:
            traversal = tarfile.TarInfo("../escape")
            traversal.size = 0
            archive.addfile(traversal)
        with self.assertRaisesRegex(InventoryError, "archive member"):
            validate_archive(tar_path, stage, stage.name)

        zip_path = self.root / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("bundle", b"first")
                archive.writestr("bundle", b"second")
        with self.assertRaisesRegex(InventoryError, "duplicate"):
            validate_archive(zip_path, stage, stage.name)

        link_path = self.root / "link.tar.gz"
        with tarfile.open(link_path, "w:gz") as archive:
            link = tarfile.TarInfo("bundle/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "target"
            archive.addfile(link)
        with self.assertRaisesRegex(InventoryError, "links"):
            validate_archive(link_path, stage, stage.name)


if __name__ == "__main__":
    unittest.main()
