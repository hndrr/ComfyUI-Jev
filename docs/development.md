# Development and publishing

[User README](../README.md) | [日本語](development.ja.md)

For contributors changing ComfyUI-Jev and maintainers releasing it to the Registry.

## Tests

Tests require ComfyUI and its dependencies. Place this repository at `ComfyUI/custom_nodes/ComfyUI-Jev`, then run from the repository root using the Python environment that runs ComfyUI:

```sh
python -m unittest discover -s tests -v
```

The regular tests do not call paid APIs. They use mocked responses to verify candidate generation, selection, and cache reuse, and run through standard nodes to Save Image using the actual ComfyUI execution engine. Checkpoint loading and trained-model computation are also mocked, so these tests do not evaluate image quality or real API judgment accuracy.

To test a separate checkout without installing it, run from a neutral directory (not the checkout, whose `nodes.py` would shadow ComfyUI's module):

```sh
cd /tmp
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/path/to/ComfyUI /path/to/ComfyUI/venv/bin/python -m unittest discover -s /path/to/ComfyUI-Jev/tests -v
```

Decisions tests cover the three OpenAI/Cloudflare presets, the router's exact payload and authentication source, all judgment tasks, malformed/refused answers, request limits, catalog exclusion, and execution/cache behavior. Tests also run the actual ComfyUI custom-node loader, check PNG batches, image cache changes, extraction offsets, and both suggestion stages. They validate the integration contract with mocks, not live model availability or multimodal inference. The optional two-request live check is documented in [Decisions inputs](modalities.md).

Each example in `examples/` has a `.workflow.json` for the UI and a matching `.api.json`. Update both when changing inputs or connections. Preserve node IDs and compatibility with existing workflows.

## Publishing to Comfy Registry

[`pyproject.toml`](../pyproject.toml) defines Registry ID `comfyui-jev`, display name `ComfyUI-Jev`, and publisher `hndr`. The release version is maintained in `[project].version`.

### Initial setup

1. Obtain a publishing API key for `hndr` in [Comfy Registry](https://registry.comfy.org). An existing key for the same publisher can be reused. If its value was not saved, create a new key and store it.
2. Add it as `REGISTRY_ACCESS_TOKEN` in [this repository's Actions Secrets](https://github.com/hndrr/ComfyUI-Jev/settings/secrets/actions). Repository secrets must be set for each repository, even when reusing the same key. This key is separate from the TypeSafe and OpenRouter keys used by the nodes.
3. Push the publishing configuration and release files to `main`. A change to `pyproject.toml` triggers the [publish workflow](../.github/workflows/publish.yml).

### Subsequent releases

Increment `[project].version` to a new `X.Y.Z` value in the same push as the release changes. For a manual run, select **Actions → Publish to Comfy Registry → Run workflow** and choose `main`.

Published versions cannot be overwritten. Rerun a failed workflow only if that version has not been published. Publishing is restricted to `main` in `hndrr/ComfyUI-Jev` and is skipped in forks.

[`.comfyignore`](../.comfyignore) excludes tests and GitHub configuration from the published archive. Runtime modules, READMEs, documentation, and example workflows remain included.

Publishing specifications: [Official publishing guide](https://docs.comfy.org/registry/publishing) / [Metadata specification](https://docs.comfy.org/registry/specifications)

## API references

- [TypeSafe](https://docs.typesafe.ai/introduction)
- [OpenRouter Decisions](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)
- [Modality evidence and extension boundaries](modalities.md)
- [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
- [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
