# Development and publishing

[User README](../README.md) | [日本語](development.ja.md)

For contributors changing ComfyUI-Jev and maintainers releasing it to the Registry.

Release policy, retries, and historical notes: [MAINTAINERS.md](../MAINTAINERS.md) (Japanese).

## Tests

Follow the [node test guide](node-tests.md) for the complete offline suite and CI setup. Tests require ComfyUI and its dependencies. From the **ComfyUI root**, use its Python environment:

```sh
python custom_nodes/ComfyUI-Jev/.github/scripts/run_node_tests.py --comfy-dir .
```

The runner selects the actual ComfyUI modules, blocks real network access, and fails on skipped tests or incomplete discovery. Running plain discovery from the Jev repository root can shadow ComfyUI's `nodes.py` with this extension's module. The tests mock paid API responses and learned-model computation; they do not measure real model accuracy.

Release automation has a separate suite that requires only Python 3.11 or later and Git:

```sh
python3 -m unittest discover -s .github/tests -v
```

Each example in `examples/` has a `.workflow.json` for the UI and a matching `.api.json`. Update both when changing inputs or connections. Preserve node IDs and compatibility with existing workflows.

## Publishing to Comfy Registry

[`pyproject.toml`](../pyproject.toml) defines Registry ID `comfyui-jev`, display name `ComfyUI-Jev`, and publisher `hndr`. The release version is maintained in `[project].version`.

### Initial setup

1. Obtain a publishing API key for `hndr` in [Comfy Registry](https://registry.comfy.org). An existing key for the same publisher can be reused. If its value was not saved, create a new key and store it.
2. Add it as `REGISTRY_ACCESS_TOKEN` in [this repository's Actions Secrets](https://github.com/hndrr/ComfyUI-Jev/settings/secrets/actions). Repository secrets must be set for each repository, even when reusing the same key. This key is separate from the TypeSafe and OpenRouter keys used by the nodes.
3. Allow the version-preparation job to push to `main` under the repository's Actions settings and branch protection.
4. Run **Actions → Publish to Comfy registry → Run workflow**, selecting `main` and `mode = sync-notes`, to backfill existing Active/Pending/Flagged Registry versions before the next new release. This updates release history and notes while preserving review status; deleted and Banned versions are skipped.

### Subsequent releases

The [publish workflow](../.github/workflows/publish.yml) runs when release changes reach `main`. Ordinary fixes automatically increment the patch version. Set `[project].version` explicitly in the PR for a new feature (minor) or a breaking change (major), including during `0.x.y` development. Versions must use `X.Y.Z` without leading zeroes or a `v` prefix. README, maintainer/development documentation, tests, and GitHub configuration changes alone do not publish.

The workflow records a preparation commit, publishes that exact commit, and creates a matching `vX.Y.Z` GitHub Release. Use English PR titles and release notes; the same notes are sent to the Registry. Runs are queued, and changes already included in a prepared release are skipped. Publishing is restricted to `main` in `hndrr/ComfyUI-Jev` and is skipped in forks.

Published packages cannot be overwritten. A manual `mode = publish` run publishes the current version without incrementing it; use it only for an unpublished, prepared version. If Registry publishing already succeeded and the version is Active/Pending, retry only the failed Release job or use `sync-notes`. If the version became Flagged before Release creation, use `sync-notes` to backfill its history and notes without changing its review status or republishing. See [the retry table](../MAINTAINERS.md#公開失敗時の再試行) for failures before version preparation.

Upload success does not establish Registry approval. Automatic Release creation accepts Active or Pending versions. Manual `sync-notes` also accepts Flagged versions and preserves review and deprecation status through the [changelog update API](https://docs.comfy.org/registry/api-reference/registry/update-changelog-and-deprecation-status-of-a-node-version); deleted and Banned versions are skipped. As of 2026-10-08, both `0.1.0` and `0.1.1` are Flagged with reason `policy-v0.5: arbitrary-file-read`. Backfilling their history does not approve them.

[`.comfyignore`](../.comfyignore) excludes tests and GitHub configuration from the published archive. Runtime modules, READMEs, maintainer instructions, documentation, and example workflows remain included.

Publishing specifications: [Official publishing guide](https://docs.comfy.org/registry/publishing) / [Metadata specification](https://docs.comfy.org/registry/specifications)

## API references

- [TypeSafe](https://docs.typesafe.ai/introduction)
- [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
- [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
