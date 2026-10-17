# Decisions input limits

Inputs are checked before paid inference. Existing text/image socket names, widget positions, and provider defaults are unchanged. The entire Interpret/Skill Choice image batch is still sent, in order; select/resize frames with existing ComfyUI nodes first. Find Box resizes its selected upload copy as before.

| Route/model | Local checks |
| --- | --- |
| TypeSafe direct | Choice ≤255 options; Score 2–10 levels; 32 MiB JSON body; finite JSON and ≤64 nested containers in state/instructions/criteria. |
| Jev through OpenRouter | Same Choice/Score limits; same 32 MiB body and JSON checks. |
| Jev images, both routes | Still PNG/JPEG, ≤16 MiB per decoded file, maximum side 1568, area 78,400–2,458,624 pixels. Direct minimum side 280; OpenRouter minimum side 256. |
| Luna Decisions | ≤128 images and ≤200 questions; accepts PNG/JPEG/WebP. |
| Clef / Clef Flash | ≤4 images, ≤64 questions, Choice 2–255, Score 2–10; existing conservative local 256 KiB encoded request budget. State text beyond roughly 2,000 tokens can be dropped upstream; a conservative byte-based notice remains visible. |
| Clef Omni | ≤4 images. Native audio/video remain unsupported by this extension. |
| Mercury Decide | ≤16 images; **local conservative 8 MB image budget**, not a published server maximum. The guide records a successful 7 MB image and failed 12/16 MB images. |
| Liquid d1 / d1-ultra | ≤8 images, ≤4.5 MB encoded request, ≤10,000 total 32×32 image patches (round each side up). |
| Other catalog Decisions models | Preserve their existing image capability gate and wire format. They do not inherit Jev/Clef limits or provider routing. Undocumented limits are left to the provider. |

There is no fixed Jev question-count cap: its total and per-question token context limits still apply. Local byte checks are not token estimates. Jev's processed-image and expanded-request limits can be exceeded even when the upload passes; those sizes are checked upstream. Errors distinguish context overflow from HTTP 413 size overflow and suggest reducing image size, batches/references, questions, or text. Provider error bodies are never echoed because they can contain request content or secrets.

The direct API's long-context experimental header is not enabled automatically. This change adds validation, not a long-context option. Standard requests retain their existing context mode.

Specifications checked 2026-10-17: [TypeSafe API](https://docs.typesafe.ai/api), [TypeSafe models](https://docs.typesafe.ai/models), [TypeSafe images](https://docs.typesafe.ai/images), [OpenRouter multimodal Decisions](https://openrouter.ai/docs/guides/community/multimodal-decisions), [Luna Decisions](https://openrouter.ai/openai/gpt-6-luna-decisions). Experimental upstream limits may change.
