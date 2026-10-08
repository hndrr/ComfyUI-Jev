"""Share release notes between GitHub Releases and existing Registry versions."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid


class API:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if not token:
            raise ValueError("An API token is required")
        self.headers["Authorization"] = f"Bearer {token}"

    def __call__(self, method, path, data=None, missing_ok=False):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        request = Request(self.base_url + path, data=payload, headers=self.headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            status = error.code
            error.close()
            if missing_ok and status == 404:
                return None
            # API responses can echo request credentials; never print their bodies.
            raise RuntimeError(f"{method} {path}: HTTP {status}") from None


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def validate_release(version, sha):
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError(f"Unsupported release version: {version}")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("A full release commit SHA is required")


def checked_tag(github, repository, version, sha):
    validate_release(version, sha)
    prefix = f"/repos/{repository}"
    ref = github("GET", f"{prefix}/git/ref/tags/v{version}", missing_ok=True)
    if ref is None:
        return None
    target = ref["object"]
    visited = set()
    while target["type"] == "tag":
        if target["sha"] in visited:
            raise ValueError("Cyclic annotated tag")
        visited.add(target["sha"])
        target = github("GET", f"{prefix}/git/tags/{target['sha']}")["object"]
    if target["type"] != "commit" or target["sha"] != sha:
        raise ValueError(f"Tag v{version} does not point to the published commit {sha}")
    return ref


def existing_release(github, repository, version):
    """Find a release, including drafts, across every page of the release list."""
    page = 1
    while True:
        releases = github("GET", f"/repos/{repository}/releases?per_page=100&page={page}")
        for release in releases:
            if release["tag_name"] == f"v{version}":
                if release["draft"]:
                    raise ValueError(f"Release v{version} is a draft; publish or remove it explicitly")
                return release
        if len(releases) < 100:
            return None
        page += 1


def release_body(github, repository, version, sha, fallback=None):
    ref = checked_tag(github, repository, version, sha)
    prefix = f"/repos/{repository}"
    release = existing_release(github, repository, version)
    if release is not None:
        if ref is None:
            raise ValueError(f"Release v{version} has no matching published tag")
        return (release.get("body") or "").strip()
    if fallback is not None:
        return fallback.strip()
    notes = github("POST", f"{prefix}/releases/generate-notes", {
        "tag_name": f"v{version}", "target_commitish": sha,
    })
    return notes["body"].strip()


def sync_release(github, registry, repository, publisher, node_id, node_version, sha, body, latest=True):
    version = node_version["version"]
    prefix = f"/repos/{repository}"
    ref = checked_tag(github, repository, version, sha)
    release = existing_release(github, repository, version)
    if release is not None:
        if ref is None:
            raise ValueError(f"Release v{version} must have a published tag at the expected commit")
        # Edited GitHub release notes are the source of truth on later syncs.
        body = (release.get("body") or "").strip()
    else:
        body = body.strip()
    if (node_version.get("changelog") or "").strip() != body:
        updated = registry("PUT", (
            f"/publishers/{quote(publisher, safe='')}/nodes/{quote(node_id, safe='')}"
            f"/versions/{quote(node_version['id'], safe='')}"
        ), {"changelog": body, "deprecated": node_version.get("deprecated", False)})
        if (updated.get("changelog") or "").strip() != body:
            raise ValueError(f"Registry did not retain the changelog for {version}")
        if updated.get("deprecated", False) != node_version.get("deprecated", False):
            raise ValueError(f"Registry did not preserve the deprecated status for {version}")
        if updated.get("status") != node_version.get("status"):
            raise ValueError(f"Registry did not preserve the review status for {version}")
    if release is None:
        if ref is None:
            github("POST", f"{prefix}/git/refs", {"ref": f"refs/tags/v{version}", "sha": sha})
        github("POST", f"{prefix}/releases", {
            "tag_name": f"v{version}", "target_commitish": sha, "name": f"v{version}",
            "body": body, "draft": False, "prerelease": False,
            "make_latest": "true" if latest else "false",
        })
    print(f"Shared release notes synchronized for v{version}")


def registered_versions(registry, node_id, include_flagged=False):
    versions = registry("GET", f"/nodes/{quote(node_id, safe='')}/versions")
    statuses = {"NodeVersionStatusActive", "NodeVersionStatusPending"}
    if include_flagged:
        statuses.add("NodeVersionStatusFlagged")
    for version in versions:
        if version.get("status") not in statuses:
            print(f"Skipping Registry version {version['version']}: {version.get('status')}")
    return [version for version in versions if version.get("status") in statuses]


def release_commit(version, history):
    if version in history:
        return history[version]["sha"]
    sha = git("log", "--first-parent", "-1", "--format=%H", f"--grep=^Prepare registry version {re.escape(version)}$")
    if not sha:
        raise ValueError(f"No verified release commit is recorded for {version}")
    return sha


def backfill(github, registry, repository, publisher, node_id, history, superseded):
    versions = sorted(
        (item for item in registered_versions(registry, node_id, include_flagged=True)
         if item["version"] not in superseded),
        key=lambda item: tuple(map(int, item["version"].split("."))),
    )
    # Resolve everything before performing any writes; deleted versions are absent.
    targets = []
    for item in versions:
        version = item["version"]
        sha = release_commit(version, history)
        validate_release(version, sha)
        config = tomllib.loads(git("show", f"{sha}:pyproject.toml"))
        if config["project"]["version"] != version:
            raise ValueError(f"Release commit has a different version than {version}")
        git("merge-base", "--is-ancestor", sha, "HEAD")
        body = release_body(github, repository, version, sha, history.get(version, {}).get("body"))
        targets.append((item, sha, body))
    for index, (item, sha, body) in enumerate(targets):
        sync_release(github, registry, repository, publisher, node_id, item, sha, body, latest=index == len(targets) - 1)


def write_outputs(path, values):
    with Path(path).open("a", encoding="utf-8") as output:
        for name, value in values.items():
            delimiter = f"notes_{uuid.uuid4().hex}"
            while delimiter in value.splitlines():
                delimiter = f"notes_{uuid.uuid4().hex}"
            output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "create", "backfill"))
    arguments = parser.parse_args()
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    repository = os.environ["GITHUB_REPOSITORY"]
    github = API(os.environ.get("GITHUB_API_URL", "https://api.github.com"), os.environ["GH_TOKEN"])
    if arguments.mode == "prepare":
        version = config["project"]["version"]
        sha = git("rev-parse", "HEAD")
        fallback = os.environ.get("RELEASE_NOTES_INPUT") or None
        body = release_body(github, repository, version, sha, fallback)
        write_outputs(os.environ["GITHUB_OUTPUT"], {"version": version, "body": body})
        return
    registry = API("https://api.comfy.org", os.environ["REGISTRY_ACCESS_TOKEN"])
    publisher, node_id = config["tool"]["comfy"]["PublisherId"], config["project"]["name"]
    if arguments.mode == "backfill":
        history = json.loads(Path(".github/release-history.json").read_text(encoding="utf-8"))
        backfill(github, registry, repository, publisher, node_id, history["releases"], history["superseded"])
    else:
        version, sha = os.environ["RELEASE_VERSION"], os.environ["RELEASE_SHA"]
        if config["project"]["version"] != version or git("rev-parse", "HEAD") != sha:
            raise ValueError("The checked-out commit must match the published version and SHA")
        matches = [item for item in registered_versions(registry, node_id) if item["version"] == version]
        if len(matches) != 1:
            raise ValueError(f"Registry version {version} is not available for release notes")
        sync_release(github, registry, repository, publisher, node_id, matches[0], sha, os.environ["RELEASE_NOTES"])


if __name__ == "__main__":
    main()
