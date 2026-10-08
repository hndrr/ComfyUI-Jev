import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("prepare_version", Path(__file__).parents[1] / "scripts/prepare_version.py")
versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(versions)


class PrepareVersionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        previous = Path.cwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, previous)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.invalid")
        self.write_version("0.1.1")
        self.before = self.commit("Initial version")

    def git(self, *args):
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()

    def write_version(self, version):
        Path("pyproject.toml").write_text(
            f'[project]\nname = "comfyui-jev"\nversion = "{version}" # Release version\n\n'
            '[tool.other]\nversion = "9.9.9"\n', encoding="utf-8",
        )

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def prepared(self, before, pushed):
        version = versions.prepare_version(before, pushed)
        source = self.git("rev-parse", "HEAD")
        return self.commit(f"Prepare registry version {version}\n\nRegistry-Source: {source}")

    def test_regular_push_increments_patch_and_preserves_other_metadata(self):
        pushed = self.commit("Fix node behavior")
        self.assertEqual(versions.prepare_version(self.before, pushed), "0.1.2")
        source = Path("pyproject.toml").read_text()
        self.assertIn('version = "0.1.2" # Release version', source)
        self.assertIn('[tool.other]\nversion = "9.9.9"', source)

    def test_explicit_minor_version_is_kept(self):
        self.write_version("0.2.0")
        pushed = self.commit("Add feature")
        self.assertEqual(versions.prepare_version(self.before, pushed), "0.2.0")
        self.assertIn('version = "0.2.0"', Path("pyproject.toml").read_text())

    def test_explicit_major_version_is_kept(self):
        self.write_version("1.0.0")
        pushed = self.commit("Change node contract")
        self.assertEqual(versions.prepare_version(self.before, pushed), "1.0.0")

    def test_rerun_skips_prepared_source(self):
        pushed = self.commit("Fix node behavior")
        self.prepared(self.before, pushed)
        self.assertIsNone(versions.prepare_version(self.before, pushed))
        self.assertIn('version = "0.1.2"', Path("pyproject.toml").read_text())

    def test_explicit_version_also_records_and_skips_source(self):
        self.write_version("0.2.0")
        pushed = self.commit("Add feature")
        self.prepared(self.before, pushed)
        self.assertIsNone(versions.prepare_version(self.before, pushed))

    def test_queued_push_included_in_latest_source_is_skipped(self):
        first = self.commit("First fix")
        second = self.commit("Second fix")
        self.prepared(self.before, first)
        self.assertIsNone(versions.prepare_version(first, second))

    def test_next_push_bumps_after_prepared_version(self):
        first = self.commit("First fix")
        prepared = self.prepared(self.before, first)
        second = self.commit("Next fix")
        self.assertEqual(versions.prepare_version(prepared, second), "0.1.3")

    def test_queued_new_push_compares_with_latest_prepared_version(self):
        first = self.commit("First fix")
        second = self.commit("Second fix")
        self.prepared(self.before, first)
        third = self.commit("Third fix")
        self.assertEqual(versions.prepare_version(second, third), "0.1.3")

    def test_latest_explicit_version_is_used_for_earlier_queued_push(self):
        first = self.commit("First fix")
        self.write_version("0.2.0")
        self.commit("Add feature")
        self.assertEqual(versions.prepare_version(self.before, first), "0.2.0")

    def test_docs_after_preparation_do_not_repeat_old_push(self):
        pushed = self.commit("Fix node behavior")
        self.prepared(self.before, pushed)
        self.commit("Update documentation")
        self.assertIsNone(versions.prepare_version(self.before, pushed))

    def test_explicit_decrease_stops_without_changing_file(self):
        self.write_version("0.1.0")
        pushed = self.commit("Lower version")
        with self.assertRaisesRegex(ValueError, "must increase"):
            versions.prepare_version(self.before, pushed)
        self.assertIn('version = "0.1.0"', Path("pyproject.toml").read_text())

    def test_current_version_cannot_drop_below_prepared_version(self):
        first = self.commit("First fix")
        self.prepared(self.before, first)
        second = self.commit("Next fix")
        self.write_version("0.1.1")
        with self.assertRaisesRegex(ValueError, "must not decrease"):
            versions.prepare_version(first, second)

    def test_invalid_semantic_versions_are_rejected(self):
        for version in ("01.2.3", "1.02.3", "1.2.03", "1.2", "v1.2.3", "1.2.3rc1", "1.2.3-beta.1", 123):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "X.Y.Z"):
                versions.version_parts(version)

    def test_malformed_source_trailer_stops_preparation(self):
        pushed = self.commit("Fix node behavior")
        self.commit("Prepare registry version 0.1.2\n\nRegistry-Source: invalid")
        with self.assertRaisesRegex(ValueError, "Invalid Registry-Source"):
            versions.prepare_version(self.before, pushed)

    def test_new_branch_event_requires_existing_baseline(self):
        with self.assertRaisesRegex(ValueError, "existing branch"):
            versions.prepare_version("0" * 40, self.before)


if __name__ == "__main__":
    unittest.main()
