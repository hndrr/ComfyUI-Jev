# Decisions inputs

[日本語](modalities.ja.md) | [Node reference](nodes.md)

## Current status

| Model through OpenRouter | Text judgments | Images in this extension |
| --- | --- | --- |
| `openai/gpt-6-luna-decisions` | Implemented; mocked contract tests | Experimental opt-in transport candidate; server decoding unverified |
| `cloudflare/clef-flash` | Implemented; mocked contract tests | Router media mapping unresolved |
| `cloudflare/clef` | Implemented; mocked contract tests | Router media mapping unresolved |

No paid inference was used to verify this implementation. Image transport is **not verified multimodal support**. The image option defaults off. The model catalog advertises text and image inputs for these models; this does not define the request encoding or establish native audio, video, or document-file support.

## Composing inputs

`Jev Interpret` and `Jev Skill Choice` keep their existing text inputs and outputs. For the Luna experiment, connect a standard ComfyUI `IMAGE` batch to `images`, select `provider=openrouter` and `openai/gpt-6-luna-decisions`, and enable `experimental_images` in advanced inputs. Connecting images without opting in fails before any request. Other model/provider combinations also fail explicitly; the Clef restriction reflects unresolved routing, not a finding that the models cannot see images.

Every frame is encoded as PNG in batch order and included in one judgment context. Images are not sampled, resized, captioned, or judged individually. Floating-point pixels are clamped to `[0, 1]` and quantized to 8 bits; grayscale, RGB, and RGBA batches are accepted. Both stages of suggestion/skill selection receive the same images. `extract` still selects numbers from the original text and preserves its offsets; it does not perform OCR.

Use existing nodes to turn video into selected frames, audio into a transcript, or documents into text/rendered pages. Frame batches preserve order but carry no timing or audio. Transcripts and extracted text connect directly to `state`/`prompt`; rendered pages can use the image experiment. There are no native `AUDIO`, `VIDEO`, or file inputs, and URLs written in the text are not fetched. This keeps preprocessing reusable and avoids adding decoders or transcription services to Jev.

## Transport evidence and boundary

The [OpenRouter API](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request) uses `{model, state, questions}` and keyed `noul`/`choice`/`score` answers. Its [Python request schema](https://github.com/OpenRouterTeam/python-sdk/blob/3ca8595f3d0898d8c9141baaee68eb30fee410c1/src/openrouter/components/decisionsrequest.py) accepts structured state, and the [official AI SDK adapter](https://github.com/OpenRouterTeam/ai-sdk-provider/blob/1b22b05352cb0f9243a6c3fdd326038dd3705544/src/evaluation/index.ts) forwards `state` unchanged. These establish client-side transport, not server media interpretation.

The [native OpenAI guide](https://developers.openai.com/api/docs/guides/decisions) accepts user messages containing `input_text` and `input_image` parts. The experiment puts that native message array inside the router's `state`:

```json
{"state": [{"role": "user", "content": [
  {"type": "input_text", "text": "Judge the attached images."},
  {"type": "input_image", "image_url": "data:image/png;base64,..."}
]}]}
```

This mapping is an explicit inference. The router's published examples show text state, and no inspected source documents translating these parts into native OpenAI image input. Native OpenAI instead uses a top-level `input` and different question/answer shapes; this extension does not switch endpoints or assume the two APIs are interchangeable. Text-only requests retain their original `state` string.

[Cloudflare's hosted Clef schema](https://developers.cloudflare.com/workers-ai/models/clef/) documents a separate `images` field. Its image limits and encoding must not be assumed to be OpenRouter's contract. A confirmed router example or a controlled live result is needed before adding its serializer. The current image code is isolated in [`media.py`](../media.py) for that change.

### OpenRouter's common multimodal guides

The additional official guides were checked on 2026-10-07:

| Guide | Documented input route and parts |
| --- | --- |
| [Image tutorial](https://openrouter.ai/blog/tutorials/send-image-to-llm/) and [image understanding](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding) | Chat Completions `messages`: `text`, then `image_url` with a URL/base64 data URL |
| [Audio](https://openrouter.ai/docs/guides/overview/multimodal/audio) | Chat Completions `messages`: `input_audio` with base64 data and format, for compatible models |
| [Video](https://openrouter.ai/docs/guides/overview/multimodal/videos) | Chat Completions `video_url`; also documents Responses `input_video` processing for compatible models |

These establish shared router media formats for those routes. None documents passing these parts through `/api/alpha/decisions`, and audio/video support depends on the selected model. They do not establish that the native OpenAI parts used by this experiment are equivalent to chat parts inside Decisions state. Consequently the candidate remains explicitly experimental, and no automatic fallback to chat or another model is added. A confirmed Decisions mapping can replace the small serializer while retaining the same ComfyUI input boundary. Native audio/video inputs still need both a model capability and a Decisions transport contract.

## Optional live verification

[`tools/check_luna_images.py`](../tools/check_luna_images.py) prepares six independently random colored circles, six choice questions, and a text-only negative control. It prints no image labels in the request text. Running it without flags reads no credentials and performs no network calls:

```sh
python tools/check_luna_images.py
```

When you explicitly authorize the cost, use the ComfyUI Python environment with `OPENROUTER_API_KEY` already set and run `python tools/check_luna_images.py --run`. This makes at most **two paid requests**, six questions each, with no retries. It does not persist the key or modify ComfyUI.

The check requires all six image choices to match the pixels with probability at least 0.8, and at least four correct-color probabilities to improve by 0.5 over the no-image control. A passing run is evidence for this candidate, model, and request at that time; an HTTP success alone is not. A failure/refusal is inconclusive and should lead to checking the mapping. This script has only been run in its offline preparation mode for this change.
