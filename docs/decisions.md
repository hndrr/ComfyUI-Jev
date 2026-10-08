# Image decisions

[日本語](decisions.ja.md) · [Node reference](nodes.md)

Set `provider` to `openrouter` and select `openai/gpt-6-luna-decisions`, `cloudflare/clef`, or `cloudflare/clef-flash` in Jev Interpret or Jev Skill Choice. The normal optional `images` input accepts ComfyUI IMAGE batches. Existing text workflows, defaults, output types, and credential handling are unchanged.

For a minimal workflow, connect **Load Image → Jev Interpret.images**, choose `task = boolean`, and ask `Does the image contain a red object?`. Connect `result` to Preview as Text. No checkpoint or text-generation node is needed.

## Composing context

Context order is always the original `state`/`prompt`, the ordered `content_json` parts, then every image in the IMAGE batch. Ranking and full-content verification use that same context. Images are encoded as PNG without resizing or selecting a subset. Use existing image resize/batch selection nodes before connecting them.

`content_json` is an optional STRING connection containing a JSON array:

```json
[
  {"type":"text","text":"This is the package front."},
  {"type":"image_url","image_url":{"url":"data:image/png;base64,<BASE64>"}}
]
```

The adapter sends text strings and image parts directly in the Decisions `state` array. Data URLs must contain actual PNG, JPEG, or WebP bytes. Remote URLs, nested image messages, and `input_image` are not this endpoint's image contract. Images are limited to 128 for Luna and 4 for each Clef model. `detail` accepts auto/low/high; Clef ignores it. These rules come from the [Decisions-specific guide](https://openrouter.ai/docs/guides/community/multimodal-decisions).

Audio, video, and file parts are rejected before sending. Use existing transcription nodes for audio, frame selection for video, and text/page extraction for documents. Connect transcripts or timestamps as text and selected frames/pages as IMAGE. This does not preserve native video timing or audio information automatically. Numeric extraction still refers only to the original state text and its original offsets.

## Limits and routing

### Connecting existing preprocessing nodes

| Source output | Connect to | What is sent |
| --- | --- | --- |
| Plain text or an audio transcription node's STRING | `state` / Skill Choice `prompt` | The unchanged text, including speaker labels and timestamps |
| A video's selected IMAGE frames | `images` | All selected frames, in batch order |
| Video frame times / subtitles | `state` or text parts in `content_json` | Explicit metadata; include frame indices matching the batch |
| Document/PDF extracted text | `state` | The extracted text; include page labels if needed |
| Document/PDF page IMAGE batch | `images` | The selected pages as images, subject to image-count limits |
| JSON analysis serialized as STRING | `state` | The original JSON string, without rewriting or discarding nested fields |

For example, connect a transcription node's STRING output directly to `state` and ask `Does the speaker agree to the proposal?`. For video, connect selected frames to `images` and use `{"frames":[{"index":0,"seconds":0},{"index":1,"seconds":1.5}],"subtitle":"Check both frames"}` as `state`. For document analysis, pass extracted text or a JSON string such as `{"page":2,"status":"approved","notes":["signed"]}` to `state`.

These paths use existing inputs and ComfyUI output types; no additional ASR, decoder, summarizer, or conversion service is introduced. A DICT output needs serialization to STRING upstream. Native audio/video/PDF uploads are not claimed. Actual ComfyUI execution tests cover STRING connections for transcripts, extracted documents and nested JSON, plus an IMAGE frame batch with separately connected timestamp/context text. External transcription, decoding and document-extraction quality is outside those tests. Long transcripts remain subject to the provider limits below.

Cloudflare's guide notes a roughly 2,000-token state-text limit with upstream truncation and an encoded-size estimate against a 65,536-token window. The extension warns above 2,000 UTF-8 text bytes as an early, conservative signal—not a tokenizer count—and rejects Cloudflare payloads above a conservative 256 KiB wire budget. It never truncates text or resizes images locally. Shorten context or use a different model when these constraints matter.

The adapter also checks upstream question/choice/score limits: [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions) permits 200 questions; [Clef](https://developers.cloudflare.com/workers-ai/models/clef/) and [Clef Flash](https://developers.cloudflare.com/workers-ai/models/clef-flash/) permit 64 questions, 2–255 choice candidates and 2–10 score levels. Candidate expansion and skill shortlists can produce multiple questions. A single candidate skips comparison questions while retaining the need, fit, and optional applicability checks.

Image requests explicitly select OpenAI for Luna and Cloudflare for Clef, through OpenRouter, with provider fallback disabled. A cheaper third-party Clef route did not pass the image experiment below. Text-only legacy routing is unchanged. The chosen image route can cost more than OpenRouter's cheapest listed provider; consult its current pricing.

`images` and `content_json` are regular cache inputs. A change to the connected content invalidates the judgment cache. Change `refresh` to request a new judgment with unchanged inputs. Older saved workflows need no migration; the added connections are optional and do not shift existing widgets.

## Recorded live verification — 2026-10-08

Ten total authorized calls used a synthetic 64×64 PNG with six randomly colored circles, with no answer key in the request text. Success required all six correct with probability ≥0.8, plus at least four increases ≥0.5 over the same model/provider's image-free control.

The four additional calls were fixed in advance: Cloudflare Clef control/image, then OpenAI Luna control/image. They exercised commit `d30bdd3`'s actual `media.prepare → api.evaluate → _post_json` path, including IMAGE tensor encoding. The test harness pinned the control to the image's provider, applied unit-price filters, and blocked retries before transmission. These test guards did not change product code. Re-encoding the original 312-byte PNG produced a pixel-identical 455-byte PNG.

| Model / actual provider | Result |
| --- | --- |
| Clef / Cloudflare | Additional control and image calls succeeded; 6/6 correct, probabilities 0.9435–0.9853, and all six improvements passed. |
| Clef Flash / Cloudflare | Earlier control and image calls succeeded; 6/6 correct, probabilities 0.9599–0.9724, and all six improvements passed. |
| Luna Decisions / OpenAI | Both formally encoded image calls returned 4/6 correct at ≥0.8, misclassifying the yellow top-right and purple bottom-right circles. The additional formal control returned HTTP 502 with `OpenAI refused to answer question "top_left"`; no valid paired control exists. The full visual test did not pass. |
| Clef / PrimeIntellect | Earlier control/image calls returned typed answers, but the image test failed (0/6 at the required probability). The image route therefore remains explicitly pinned to Cloudflare. |

Eight successful calls reported a total USD 0.000970606. Both failed Luna controls have unknown charges; retaining USD 0.0032768 for each gives USD 0.007524206 accounted against the authorized USD 0.10. All ten allowed attempts are consumed, with no retries or extra requests. The first failed control used an older nested-state hypothesis and its error body was unavailable; the later refusal does not prove the first failure had the same cause.

These are measured results, separate from documented API support and offline tests. Clef and Flash passed only this single small PNG test. Luna did not meet the recognition criterion, despite accepting the image request. Large batches, JPEG/WebP recognition, and broader real-world accuracy remain unverified. No model is hidden behind an experimental switch.

## Maintenance

`media.py` normalizes ComfyUI inputs without provider logic. `decisions.py` owns the wire format, model limits, and image provider selection. `api.py` remains the only network transport. `nodes.py` exposes the two optional connections. New model support should update the small model table and relevant contract tests.
