"""Read local SKILL.md files for selection."""

import hashlib
import os
from pathlib import Path
import re

import yaml

from .semantics import dumps


def read_skills(directory, base_dir):
    if not directory.strip():
        raise ValueError("Jev Skill Choice: specify a directory containing SKILL.md files")
    root = Path(directory.strip()).expanduser()
    if not root.is_absolute():
        root = Path(base_dir) / root
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Jev Skill Choice: directory does not exist: {root}")
    paths = set()
    visited = set()
    for current, directories, files in os.walk(root, followlinks=True):
        resolved = Path(current).resolve()
        if resolved in visited:
            directories.clear()
            continue
        visited.add(resolved)
        directories.sort()
        if "SKILL.md" in files:
            paths.add((Path(current) / "SKILL.md").resolve())
    records = []
    for path in sorted(paths):
        content = path.read_text(encoding="utf-8-sig")
        if not content.strip():
            raise ValueError(f"Jev Skill Choice: empty Skill file: {path}")
        metadata = {}
        if re.match(r"\A---[ \t]*\n", content):
            front = re.match(r"\A---[ \t]*\n(.*?)^(?:---|\.\.\.)[ \t]*(?:\n|$)", content, re.DOTALL | re.MULTILINE)
            if not front:
                raise ValueError(f"Jev Skill Choice: unclosed front matter: {path}")
            try:
                metadata = yaml.safe_load(front[1])
            except yaml.YAMLError as error:
                raise ValueError(f"Jev Skill Choice: invalid front matter in {path}: {error}") from None
            if metadata is None:
                metadata = {}
            if not isinstance(metadata, dict):
                raise ValueError(f"Jev Skill Choice: front matter must be a mapping: {path}")
        name = metadata.get("name", path.parent.name)
        description = metadata.get("description", content)
        for key, value in (("name", name), ("description", description)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Jev Skill Choice: {key} must be nonempty text: {path}")
        records.append({"name": name, "path": str(path), "description": description, "content": content})
    return records


def fingerprint(directory, base_dir):
    return hashlib.sha256(dumps(read_skills(directory, base_dir)).encode("utf-8")).hexdigest()


def render_skills(records):
    return "\n\n".join(
        f"# Skill: {item['name']}\n\nApplication priority: {item['strength']:g} on a 0–2 scale.\n\n{item['content']}"
        for item in records
    )
