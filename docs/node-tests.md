# Node tests

[日本語](node-tests.ja.md) · [Development guide](development.md)

Tests require ComfyUI and its dependencies. Place this repository at `ComfyUI/custom_nodes/ComfyUI-Jev`, then run from the ComfyUI root using its Python environment:

```sh
python custom_nodes/ComfyUI-Jev/.github/scripts/run_node_tests.py --comfy-dir .
```

The regular tests do not call paid APIs. They use mocked responses to verify candidate generation, selection, and cache reuse, and run through standard nodes to Save Image using the actual ComfyUI execution engine. Checkpoint loading and trained-model computation are also mocked, so these tests do not evaluate image quality or real API judgment accuracy.

The [Test nodes workflow](../.github/workflows/tests.yml) runs automatically on every pull request and supports manual dispatch. It uses Python 3.12, a pinned ComfyUI 0.39.0 checkout, and CPU-only PyTorch on a temporary GitHub-hosted runner. Dependencies are installed only on that runner; no API secrets or model weights are needed. The runner blocks real network calls during imports and tests, requires all seven test modules (at least the current 89 tests), and fails on skipped tests or incomplete discovery. This includes the real ComfyUI validation/execution tests in `test_execution.py` and `test_skill_workflow.py`, plus the multimodal adapter tests. Update the minimum count deliberately if tests are removed or consolidated. Release workflows are separate.

The same workflow runs `node .github/scripts/test_model_picker.mjs` from this repository to check provider switching and saved/custom model compatibility in the shipped frontend selector. It uses Node.js supplied by the GitHub runner and needs no npm dependencies.
