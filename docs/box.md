# Find a subject and use its region

[README](../README.md) · [Node reference](nodes.md) · [Example workflow](../examples/05_find_box.workflow.json)

Use **Jev Find Box** to describe one subject and **Jev Box Region** to work with its rectangular region. Both nodes take the same original `images` connection. Choose `provider=openrouter` (the default) or `provider=typesafe`. Set that provider's key in ComfyUI's launch environment and leave `api_key` empty. Keys entered in widgets are saved with workflows and history; remove them before sharing.

## Practical workflows

| Goal | Connections and settings |
| --- | --- |
| Crop a product or subject | Load Image → Find Box → Box Region → Save Image (`crop`). Try `the red bottle on the left`; add 16 pixels of `padding` for context. |
| Edit a region | Connect Box Region's `mask` and the original image to an inpainting or regional-conditioning workflow. White pixels select the entire rectangle, including background. Use a segmentation node after localization if you need a silhouette. |
| Inspect localization | Connect `preview` to Preview Image and `details` to Preview as Text. The outline is drawn on a copy; it never enters the crop. Preview masks through Convert Mask to Image. |

Load the [OpenRouter workflow](../examples/05_find_box.workflow.json) or [TypeSafe direct workflow](../examples/06_find_box_typesafe.workflow.json), select an image in Load Image, and describe the target. Matching API graphs are provided for [OpenRouter](../examples/05_find_box.api.json) and [TypeSafe](../examples/06_find_box_typesafe.api.json). None contains a credential or bundled user image.

For regional editing of a batch, use **Image From Batch** to select one frame before both Box nodes and the editor, then leave `image_index=0`. The mask output covers one frame; connecting it to an entire batch downstream can broadcast that mask to other frames.

## Jev Find Box

Required controls are `images`, `target`, `image_index` (default 0), `model`, `api_key`, and `refresh`. Only the selected zero-based batch frame is uploaded. Other frames are untouched; this node does not silently detect only the first frame or combine unrelated crops into a batch. Select another index, or use another Find Box/Box Region pair, for another frame or target.

Select a provider, then its Box model. The model list switches automatically:

| Provider | Box models | Environment key |
| --- | --- | --- |
| `openrouter` (default) | `typesafe/jev-1.14` (pinned default), `~typesafe/jev-latest` | `OPENROUTER_API_KEY` |
| `typesafe` (direct) | `jev-1.14.0` (pinned), `jev-preview` | `TYPESAFE_API_KEY` |

Jev 1.13 and other Decisions models cannot answer Box questions. TypeSafe's `jev-latest` currently resolves to 1.13, so it is deliberately excluded here. The direct API key is separate from an OpenRouter key; one is never used as the other's fallback. The optional provider control is appended after existing controls, and older API graphs without it continue to use OpenRouter.

Direct requests go to `https://api.typesafe.ai/v1/systemone`. Images are converted to PNG assets with unique references in `state` and raw base64 in `assets`; each image request includes `TypeSafe-Danger-Zone-Images: true`. There is no separate asset upload. Direct input must be still PNG/JPEG, 280–1568 pixels per side, at most 16 MiB per file and 32 MiB for the encoded request. The API also enforces 32 MiB limits after image processing and reference expansion; those server-side sizes can exceed the upload size. Invalid formats, dimensions, and locally measurable size limits fail before transport. Existing Interpret/Skill Choice image inputs also use this shared direct adapter; their text-only request format stays unchanged.

The two OpenRouter Box models retain their verified image support even if live or cached catalog metadata omits `image`. Other models still require image support in the catalog.

An upload copy is resized to Jev's documented dimensions when necessary: TypeSafe direct requires sides of at least 280 pixels; OpenRouter requires at least 256 and area of at least 78,400 pixels. Both paths cap each side at 1568 pixels. Original pixels are retained for cropping. Very narrow panoramas that cannot fit the allowed aspect ratio require explicit cropping or padding before both Box nodes. `upload_size` records the actual size sent. Enlarging a small image does not recover missing detail.

Outputs:

- `box_data` (DICT): selected frame index, batch size, original dimensions, target, provider, model, usage, accepted `box`, `raw_box`, gate settings, and rejection `reason`.
- `found` (BOOLEAN): true only when **both** the Noul presence probability and Box confidence meet their thresholds.
- `confidence` (FLOAT): Box localization confidence; it is not presence probability. Read `presence_probability` from `box_data` for that separate value.

Find Box always asks a Noul presence question alongside Box in one request. Advanced defaults are `presence_threshold=0.8` and `min_confidence=0.5`. These are adjustable starting points, not calibrated accuracy guarantees. Box can confidently propose a rectangle for an absent target. Inspect the preview and tune thresholds on representative images. Each question returns one rectangle; describe a distinguishable instance when several objects look alike.

## Jev Box Region

Connect the same `images` batch and Find Box's `box_data`. `padding` (default 0) adds original-image pixels on every side. Results stay inside the image. Changing padding or missing-target behavior reruns only local operations; change Find Box's `refresh` to request a fresh judgment.

Outputs are a one-frame `crop` IMAGE, a full-original-size `mask` [1,H,W], a full-size `preview` IMAGE, and `details` DICT. The mask is 1 inside the effective rectangle and 0 outside. It is a hard rectangular mask, not a boundary-accurate segmentation.

The default `on_missing=error` stops before cropping when a gate rejects the result. For inspection or workflows that explicitly handle `found=false`, choose `keep_image`: it returns the selected original frame, a zero mask, an unchanged preview, and null effective coordinates. It never uses rejected raw edges to choose a crop. Malformed, missing, nonfinite, inverted, or out-of-range API coordinates cause an error even if presence is low.

Coordinates start at the top left. `box`/`raw_box` use normalized edges in [0,1]. `details.pixels` gives integer `left`, `top`, `right`, `bottom`, `width`, and `height` after padding. Right/bottom are exclusive: crop = `image[top:bottom, left:right]`. Left/top round outward with floor; right/bottom use ceil. `effective_box` converts those exact pixel edges back to normalized coordinates. Crop, mask, outline, and effective coordinates always use the same rectangle. Source-size or batch-count mismatches raise an error. A local fingerprint also rejects different selected pixels, changed data types, and reordered frames even when dimensions match. Connect the same original image batch to both nodes and rerun Find Box after changing it.

## Verification and upstream references

Offline tests mock API answers, check malformed responses and both gates, exercise small/large upload copies, crop/mask geometry, selected-frame mapping, and actual ComfyUI execution/cache reuse for both provider examples. Direct transport tests verify assets, headers, pinned/preview model IDs, credentials, size limits, and separation from OpenRouter. Text-only TypeSafe requests retain their existing format.

On 2026-10-17, two real OpenRouter requests tested the installed implementation using locally drawn 512×384 PNGs and `typesafe/jev-1.14`. Both returned HTTP 200 from TypeSafe, reporting model `typesafe/jev-1.14-20261014`:

| Synthetic fixture | Result |
| --- | --- |
| Red rectangle present | Presence 0.97, Box confidence 0.87, `found=true`; box overlap with the known rectangle (IoU) was 0.8793. Crop pixels, padded coordinates, rectangular mask, and separate preview agreed. |
| Red rectangle absent | Presence 0.03, Box confidence 0.84, `found=false`; default crop stopped, and explicit `keep_image` returned an unchanged image, zero mask, and null effective coordinates. |

The two inference calls used 1,785 input tokens and reported a total USD 0.00007497, with no retries. The existing OpenRouter credential was loaded from Stability Matrix's launch environment settings without displaying or persisting its value. No private image was uploaded. These simple shape tests confirm the request/response path and gate behavior for these fixtures; they do not establish accuracy on photos, ambiguous subjects, or small objects.

TypeSafe direct live inference has **not** been run: no `TYPESAFE_API_KEY` was found in the task environment, Stability Matrix launch settings, or checked shell/environment files. Direct behavior is covered with mocked API transport; the live results above apply only to OpenRouter.

Official specifications: [TypeSafe Box](https://docs.typesafe.ai/primitives/box), [TypeSafe images](https://docs.typesafe.ai/images), [OpenRouter Jev Box questions](https://openrouter.ai/docs/guides/community/jev#box-questions), and [OpenRouter multimodal Decisions](https://openrouter.ai/docs/guides/community/multimodal-decisions). Images and Box are experimental upstream features.
