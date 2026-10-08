import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs


SPEC = importlib.util.spec_from_file_location("release_notes", Path(__file__).parents[1] / "scripts/release_notes.py")
notes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notes)
SHA = "a" * 40
REPO = "hndrr/ComfyUI-Jev"


class GitHub:
    def __init__(self):
        self.refs = {}
        self.releases = {}
        self.tags = {}
        self.calls = []

    def __call__(self, method, path, data=None, missing_ok=False):
        self.calls.append((method, path, data))
        route = path.removeprefix(f"/repos/{REPO}/")
        if method == "GET" and route.startswith("git/ref/tags/"):
            return self.refs.get(route.removeprefix("git/ref/tags/"))
        if method == "GET" and route.startswith("git/tags/"):
            return self.tags[route.removeprefix("git/tags/")]
        if method == "GET" and route.startswith("releases/tags/"):
            release = self.releases.get(route.removeprefix("releases/tags/"))
            return None if release is None or release["draft"] else release
        if method == "GET" and route.startswith("releases?"):
            query = parse_qs(route.split("?", 1)[1])
            page, size = int(query["page"][0]), int(query["per_page"][0])
            releases = [dict(release, tag_name=tag) for tag, release in self.releases.items()]
            return releases[(page - 1) * size:page * size]
        if method == "POST" and route == "git/refs":
            tag = data["ref"].removeprefix("refs/tags/")
            self.refs[tag] = {"object": {"type": "commit", "sha": data["sha"]}}
            return self.refs[tag]
        if method == "POST" and route == "releases":
            self.releases[data["tag_name"]] = data.copy()
            return data.copy()
        if method == "POST" and route == "releases/generate-notes":
            return {"body": f"## Changes\n\nGenerated for {data['tag_name']} at {data['target_commitish']}"}
        raise AssertionError((method, path, data))


class Registry:
    def __init__(self, versions):
        self.versions = versions
        self.calls = []

    def __call__(self, method, path, data=None, missing_ok=False):
        self.calls.append((method, path, data))
        if method == "GET" and path == "/nodes/comfyui-jev/versions":
            return self.versions
        if method == "PUT":
            for version in self.versions:
                if path == f"/publishers/hndr/nodes/comfyui-jev/versions/{version['id']}":
                    version.update(data)
                    return version.copy()
        raise AssertionError((method, path, data))


def node(version="1.2.2", deprecated=False):
    return {"version": version, "id": f"uuid-{version}", "changelog": "", "deprecated": deprecated,
            "status": "NodeVersionStatusActive"}


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        self.github = GitHub()
        self.item = node(deprecated=True)
        self.registry = Registry([self.item])

    def sync(self, body="## Changes\n\n- シーン検出を修正。"):
        with contextlib.redirect_stdout(io.StringIO()):
            notes.sync_release(self.github, self.registry, REPO, "hndr", "comfyui-jev", self.item, SHA, body)

    def test_new_release_has_same_body_and_published_commit(self):
        self.sync()
        release = self.github.releases["v1.2.2"]
        self.assertEqual(release["body"], self.item["changelog"])
        self.assertEqual(self.github.refs["v1.2.2"]["object"]["sha"], SHA)
        self.assertEqual(release["target_commitish"], SHA)
        self.assertTrue(self.item["deprecated"])
        self.assertEqual([call[0] for call in self.registry.calls], ["PUT"])

    def test_rerun_does_not_create_or_update_twice(self):
        self.sync()
        github_writes = len([call for call in self.github.calls if call[0] == "POST"])
        registry_writes = len(self.registry.calls)
        self.sync()
        self.assertEqual(len([call for call in self.github.calls if call[0] == "POST"]), github_writes)
        self.assertEqual(len(self.registry.calls), registry_writes)

    def test_edited_github_notes_are_copied_to_registry(self):
        self.sync()
        self.github.releases["v1.2.2"]["body"] = "## Fixes\n\n- Edited release comment."
        self.sync("old generated notes")
        self.assertEqual(self.item["changelog"], self.github.releases["v1.2.2"]["body"])
        self.assertEqual(len([call for call in self.github.calls if call[0] == "POST"]), 2)

    def test_wrong_tag_stops_before_any_write(self):
        self.github.refs["v1.2.2"] = {"object": {"type": "commit", "sha": "b" * 40}}
        with self.assertRaisesRegex(ValueError, "does not point"):
            self.sync()
        self.assertEqual(self.registry.calls, [])
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))

    def test_annotated_tag_is_resolved_to_commit(self):
        self.github.refs["v1.2.2"] = {"object": {"type": "tag", "sha": "b" * 40}}
        self.github.tags["b" * 40] = {"object": {"type": "commit", "sha": SHA}}
        self.sync()
        self.assertIn("v1.2.2", self.github.releases)
        self.assertFalse(any(call[1].endswith("git/refs") and call[0] == "POST" for call in self.github.calls))

    def test_draft_is_not_overwritten_or_published(self):
        self.github.refs["v1.2.2"] = {"object": {"type": "commit", "sha": SHA}}
        self.github.releases["v1.2.2"] = {"draft": True, "body": "Unfinished notes"}
        with self.assertRaisesRegex(ValueError, "is a draft"):
            self.sync()
        self.assertEqual(self.registry.calls, [])

    def test_draft_on_second_page_stops_before_any_write(self):
        self.github.releases = {f"v0.0.{index}": {"draft": False, "body": "Older notes"}
                                for index in range(100)}
        self.github.releases["v1.2.2"] = {"draft": True, "body": "Unfinished notes"}
        self.assertIsNone(self.github("GET", f"/repos/{REPO}/releases/tags/v1.2.2"))
        with self.assertRaisesRegex(ValueError, "is a draft"):
            self.sync()
        self.assertEqual(self.registry.calls, [])
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))
        self.assertTrue(any("page=2" in call[1] for call in self.github.calls))

    def test_prepare_rejects_draft_without_creating_notes(self):
        self.github.releases["v1.2.2"] = {"draft": True, "body": "Unfinished notes"}
        with self.assertRaisesRegex(ValueError, "is a draft"):
            notes.release_body(self.github, REPO, "1.2.2", SHA, "Replacement notes")
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))

    def test_backfill_checks_all_drafts_before_any_write(self):
        history = {version: {"sha": SHA, "body": "Historical notes"} for version in ("1.0.0", "1.2.2")}
        registry = Registry([node(version) for version in history])
        self.github.releases["v1.2.2"] = {"draft": True, "body": "Unfinished notes"}
        configs = iter(['[project]\nversion = "1.0.0"\n', '[project]\nversion = "1.2.2"\n'])
        def fake_git(*args):
            return next(configs) if args[0] == "show" else ""
        with patch.object(notes, "git", side_effect=fake_git):
            with self.assertRaisesRegex(ValueError, "is a draft"):
                notes.backfill(self.github, registry, REPO, "hndr", "comfyui-jev", history, {})
        self.assertFalse(any(call[0] == "PUT" for call in registry.calls))
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))

    def test_prepare_uses_existing_notes_without_regenerating(self):
        self.sync()
        body = notes.release_body(self.github, REPO, "1.2.2", SHA)
        self.assertEqual(body, self.item["changelog"])
        self.assertFalse(any(call[1].endswith("generate-notes") for call in self.github.calls))

    def test_backfill_uses_verified_history_and_marks_only_newest_latest(self):
        history = json.loads((Path(__file__).parents[1] / "release-history.json").read_text())
        registry = Registry([node(version, deprecated=version == "0.1.0") for version in history["releases"]])
        version_by_sha = {item["sha"]: version for version, item in history["releases"].items()}
        def fake_git(*args):
            if args[0] == "show":
                return f'[project]\nversion = "{version_by_sha[args[1].split(":")[0]]}"\n'
            return ""
        with patch.object(notes, "git", side_effect=fake_git), contextlib.redirect_stdout(io.StringIO()):
            notes.backfill(self.github, registry, REPO, "hndr", "comfyui-jev", history["releases"], history["superseded"])
        self.assertEqual(set(self.github.releases), {"v0.1.0", "v0.1.1"})
        self.assertEqual(self.github.releases["v0.1.0"]["make_latest"], "false")
        self.assertEqual(self.github.releases["v0.1.1"]["make_latest"], "true")
        for item in registry.versions:
            self.assertEqual(item["changelog"], self.github.releases[f"v{item['version']}"]["body"])
            self.assertEqual(item["deprecated"], item["version"] == "0.1.0")

    def test_backfill_skips_flagged_deleted_and_superseded_versions(self):
        history = {"0.1.1": {"sha": SHA, "body": "- Add MIT licensing."}}
        registry = Registry([
            node("0.1.1"),
            dict(node("0.1.0"), status="NodeVersionStatusFlagged"),
            dict(node("0.0.9"), status="NodeVersionStatusDeleted"),
            node("0.0.8"),
        ])
        with patch.object(notes, "git", return_value='[project]\nversion = "0.1.1"\n'), contextlib.redirect_stdout(io.StringIO()):
            notes.backfill(self.github, registry, REPO, "hndr", "comfyui-jev", history, {"0.0.8": "0.1.1"})
        self.assertEqual(set(self.github.releases), {"v0.1.1"})
        self.assertEqual([item["changelog"] for item in registry.versions[1:]], ["", "", ""])

    def test_pending_versions_can_sync_without_becoming_approved(self):
        self.item["status"] = "NodeVersionStatusPending"
        self.assertEqual(notes.registered_versions(self.registry, "comfyui-jev"), [self.item])
        self.sync()
        self.assertEqual(self.item["status"], "NodeVersionStatusPending")

    def test_backfill_missing_commit_stops_before_any_write(self):
        registry = Registry([node("0.1.2")])
        with patch.object(notes, "git", return_value=""), self.assertRaisesRegex(ValueError, "No verified release commit"):
            notes.backfill(self.github, registry, REPO, "hndr", "comfyui-jev", {}, {})
        self.assertFalse(any(call[0] == "PUT" for call in registry.calls))
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))

    def test_backfill_mismatched_version_stops_before_any_write(self):
        registry = Registry([node("0.1.1")])
        with patch.object(notes, "git", return_value='[project]\nversion = "0.2.0"\n'), self.assertRaisesRegex(ValueError, "different version"):
            notes.backfill(self.github, registry, REPO, "hndr", "comfyui-jev", {"0.1.1": {"sha": SHA, "body": "Notes"}}, {})
        self.assertFalse(any(call[0] == "PUT" for call in registry.calls))
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))

    def test_backfill_wrong_tag_in_later_version_stops_before_any_write(self):
        history = {version: {"sha": SHA, "body": "Historical notes"} for version in ("0.1.0", "0.1.1")}
        registry = Registry([node(version) for version in history])
        self.github.refs["v0.1.1"] = {"object": {"type": "commit", "sha": "b" * 40}}
        configs = iter(['[project]\nversion = "0.1.0"\n', '[project]\nversion = "0.1.1"\n'])
        def fake_git(*args):
            return next(configs) if args[0] == "show" else ""
        with patch.object(notes, "git", side_effect=fake_git), self.assertRaisesRegex(ValueError, "does not point"):
            notes.backfill(self.github, registry, REPO, "hndr", "comfyui-jev", history, {})
        self.assertFalse(any(call[0] == "PUT" for call in registry.calls))
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))

    def test_prepare_accepts_manual_release_notes(self):
        body = notes.release_body(self.github, REPO, "0.1.2", SHA, "- Fix candidate scoring.")
        self.assertEqual(body, "- Fix candidate scoring.")
        self.assertFalse(any(call[0] == "POST" for call in self.github.calls))

    def test_invalid_release_version_or_sha_stops_before_api_write(self):
        for version, sha in (("01.1.2", SHA), ("1.2.3-beta.1", SHA), ("1.2.3", "main")):
            with self.subTest(version=version, sha=sha), self.assertRaises(ValueError):
                notes.release_body(self.github, REPO, version, sha)
        self.assertEqual(self.github.calls, [])

    def test_api_only_treats_404_as_missing_and_hides_error_body(self):
        api = notes.API("https://api.example.invalid", "secret-token")
        for status in (401, 403, 500):
            error = HTTPError("https://api.example.invalid/test", status, "failure", {}, io.BytesIO(b"secret-token"))
            with patch.object(notes, "urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}") as caught:
                    api("GET", "/test", missing_ok=True)
                self.assertNotIn("secret-token", str(caught.exception))
        with patch.object(notes, "urlopen", side_effect=HTTPError("url", 404, "missing", {}, None)):
            self.assertIsNone(api("GET", "/test", missing_ok=True))

    def test_multiline_output_preserves_note_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output"
            body = "## Changes\n\n- 日本語の更新内容。\n- Another change."
            notes.write_outputs(path, {"body": body})
            lines = path.read_text().splitlines()
            delimiter = lines[0].split("<<", 1)[1]
            self.assertEqual(lines[-1], delimiter)
            self.assertEqual("\n".join(lines[1:-1]), body)


if __name__ == "__main__":
    unittest.main()
