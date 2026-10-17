# Evaluate an image against a reference

Use **Jev Reference Evaluate** for product consistency, a desired visual appearance, or a reference-based quality check. Connect the image to judge to `images`, and the example/reference to **`reference_images`**. Describe the comparison: “Does the evaluation image show the same bottle as the reference? Ignore the background.”

- [OpenRouter match example](../examples/07_reference_evaluate.workflow.json) / [API graph](../examples/07_reference_evaluate.api.json)
- [TypeSafe rubric example](../examples/08_reference_evaluate_typesafe.workflow.json) / [API graph](../examples/08_reference_evaluate_typesafe.api.json)

Choose an image in each Load Image node. The examples contain filenames only, no image or credential. Set `OPENROUTER_API_KEY` or `TYPESAFE_API_KEY` in the environment that launches ComfyUI, matching `provider`; leave `api_key` empty. Entered widget keys are saved with workflows/history.

## Controls and outputs

`image_index` and `reference_index` select one frame independently, starting at 0. **Exactly those two frames** are sent. A reference batch is not automatically paired, broadcast, averaged, or searched. Use another node/index pair to evaluate another frame. The original tensors are retained; upload copies are resized within Jev's provider-specific dimensions. Extreme aspect ratios require explicit cropping/padding first.

| Control | Meaning |
| --- | --- |
| `task=match` (default) | Noul probability that the evaluation image meets your comparison. A value of 0.95 means a 0.95 yes probability. Noul has no separate confidence. |
| `task=score` | Three descriptive levels: does not satisfy, partly satisfies, closely satisfies the task compared with the reference. The expected score is normalized to 0–1. This value is a rubric position, **not** a match probability. |
| `reference_placement=instructions` (default) | Put the reference image in the question as the comparison example. |
| `reference_placement=criteria` | Put it in the positive Noul criterion or the highest Score level as the desired example. |
| `threshold=0.8` | Minimum `value` to accept, inclusive. Tune on your own examples. |
| `min_confidence=0.5` | Additional Score-only gate, inclusive. It does not change Noul decisions. |
| `refresh` | Change to request a fresh judgment. Keep fixed to use ComfyUI's cache. |

Outputs are `value` (FLOAT), `accepted` (BOOLEAN), `details` (DICT), and `response_json` (STRING). Connect `accepted` to an existing conditional/switch workflow, or use `value` for ranking. `details` records both frame indices, source dimensions/fingerprints, upload sizes, task, provider/model, gate settings, and rejection reason. The ordinary details preview omits image-heavy Score legends; the complete API response remains in `response_json`.

Changing node settings, including thresholds, changes the node's cache key and may make a new request. For local retuning without inference, keep the evaluation fixed and compare its `value` downstream using existing math/condition nodes.

## Provider support and image roles

The evaluation image is explicitly named **`evaluation_image` in state**. The reference appears only in the question's instructions or rubric. The question tells Jev to evaluate the state image rather than the reference itself. Keeping these roles distinct matters when backgrounds, styles, or subject positions differ.

Both Jev 1.13 and 1.14 support these image placements. The node offers pinned `typesafe/jev-1.14` on OpenRouter by default, plus `~typesafe/jev-latest` and `typesafe/jev-1.13`. Direct TypeSafe offers `jev-1.14.0`, `jev-1.13.0`, `jev-preview`, and `jev-latest`. The provider selector switches compatible model IDs. No general VLM/chat models are offered.

Direct TypeSafe converts images in state **and questions** into raw-base64 assets with unique references and sends the experimental image header. OpenRouter Jev receives nested `image_url` data parts. Luna, Clef, and other Decisions models are rejected for this node: the current documented support for images inside instructions/criteria is Jev-specific. Their existing top-level image input in Interpret/Skill Choice stays available.

The [input limits](input-limits.md) apply to both images and the whole request, including references inside questions. Unsupported models, invalid indices, malformed answers, and measured size violations fail explicitly. A rejected comparison returns `accepted=false` with a reason; it does not select an arbitrary region or generate/modify an image. These are experimental semantic judgments; inspect results and validate thresholds on representative examples.

Official specifications: [TypeSafe image placement](https://docs.typesafe.ai/images#image-placement), [OpenRouter image part format](https://openrouter.ai/docs/guides/community/multimodal-decisions#image-part-format), [Noul](https://docs.typesafe.ai/primitives/noul), [Score](https://docs.typesafe.ai/primitives/score). Direct live inference remains unrun unless an existing TypeSafe credential is available; offline tests exercise both providers with mocked transport.

## Verification

On 2026-10-17, two real requests used synthetic 512×384 shape images and `typesafe/jev-1.14` through OpenRouter. The instructions-reference match returned 0.95 (`accepted=true`); the criteria-reference mismatch returned 0.01 (`accepted=false`). Both were HTTP 200 from TypeSafe, reporting `typesafe/jev-1.14-20261014`. No retries were allowed. Reported total cost was USD 0.000129192 for 3,076 input tokens. This verifies the two image placements for these fixtures, not photographic quality, calibrated thresholds, or Score accuracy. Score is covered offline with mocked transport. No private image was used.

Direct TypeSafe live inference remains **unrun**: no existing key was found in the checked task/launcher configuration. Assets, headers, image roles, model restrictions, and rubric parsing are tested with mocks.

## Credentials when switching providers

Changing `provider` clears the manually entered `api_key` widget on Interpret, Skill Choice, Find Box, and Reference Evaluate. Switching back does not restore an old key. Loading a saved provider/key pair preserves that pair; use environment keys to avoid saving credentials in a workflow. The backend rejects a key matching the other provider's environment variable, an OpenRouter-prefixed key sent to TypeSafe, and endpoint/provider/credential-variable mismatches. An arbitrary API-supplied token has no reliable provider identity; API callers must supply the selected provider's own credential.

The provider-switch path was reproduced and tested with dummy values only. This is a preventive fix; no actual key disclosure was established.
