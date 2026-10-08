"""Prepare a Registry version for a push without repeating covered changes."""

import os
from pathlib import Path
import re
import subprocess
import tomllib


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def version_parts(version):
    if not isinstance(version, str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("project.version must be a semantic version in X.Y.Z format")
    return tuple(map(int, version.split(".")))


def version_at(revision):
    version = tomllib.loads(git("show", f"{revision}:pyproject.toml"))["project"]["version"]
    version_parts(version)
    return version


def prepare_version(before_sha, push_sha):
    for revision in (before_sha, push_sha):
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or revision == "0" * 40:
            raise ValueError("Version preparation requires an existing branch and full push commit SHAs")
    before, pushed = version_at(before_sha), version_at(push_sha)
    if pushed != before and version_parts(pushed) <= version_parts(before):
        raise ValueError("The explicit project.version must increase")

    prepared = git("log", "--first-parent", "-1", "--format=%H", "--grep=^Registry-Source: ")
    baseline = before
    if prepared:
        message = git("show", "-s", "--format=%B", prepared)
        source_match = re.search(r"(?m)^Registry-Source: ([0-9a-f]{40})$", message)
        if source_match is None:
            raise ValueError("Invalid Registry-Source: expected a full commit SHA")
        covered = subprocess.run(["git", "merge-base", "--is-ancestor", push_sha, source_match[1]])
        if covered.returncode == 0:
            return None
        if covered.returncode != 1:
            raise ValueError("Could not compare the prepared release source")
        baseline = version_at(prepared)

    path = Path("pyproject.toml")
    source = path.read_text(encoding="utf-8")
    version = tomllib.loads(source)["project"]["version"]
    current_parts, baseline_parts = version_parts(version), version_parts(baseline)
    if current_parts < baseline_parts:
        raise ValueError("The current project.version must not decrease")
    if current_parts > baseline_parts:
        return version

    major, minor, patch = current_parts
    new_version = f"{major}.{minor}.{patch + 1}"
    project = re.search(r"(?ms)^\[project\][^\n]*\n.*?(?=^\[|\Z)", source)
    updated, count = re.subn(
        rf"""(?m)^([ \t]*version[ \t]*=[ \t]*)(["']){re.escape(version)}\2""",
        lambda match: f"{match[1]}{match[2]}{new_version}{match[2]}",
        project[0],
    )
    if count != 1:
        raise ValueError("Expected one project.version assignment")
    path.write_text(source[:project.start()] + updated + source[project.end():], encoding="utf-8")
    return new_version


if __name__ == "__main__":
    version = prepare_version(os.environ["BEFORE_SHA"], os.environ["PUSH_SHA"])
    print("skip" if version is None else version)
