# [WORK IN PROGRESS] ComfyUI-Jev

English | [日本語](README.ja.md)

Custom nodes that generate creative options from text, use Jev to select the best match for your intent, and pass the result into an existing ComfyUI workflow.

This package provides **OpenRouter Text** for text generation, **Jev Interpret** for judgments, and **Jev Skill Choice** for selecting local Skills. Use standard nodes for text formatting, number conversion, branching, and image generation. You do not need to write schemas or candidate IDs by hand.

```text
Text → Format Text → OpenRouter Text (candidates)
  └─────────────────────────────┐
                               Jev Interpret → CLIP Text Encode → KSampler → VAE Decode → Save Image
```

An OpenRouter text model generates different creative options, and Jev compares them using your judgment instructions. For example, it can assess whether each option's lighting, materials, and composition fit a brief for a refined but approachable product photograph that feels part of everyday life.

The documentation and example workflows use English by default. Japanese documentation is available in [README.ja.md](README.ja.md).

## Installation and API keys

This package uses the ComfyUI v0.36.0 V3 API. Place this directory at `custom_nodes/ComfyUI-Jev` and restart ComfyUI. No custom JavaScript or additional dependencies are required.

To use OpenRouter, set the key in the environment that starts ComfyUI:

```sh
export OPENROUTER_API_KEY="your-key"
```

Set `provider` to `openrouter` in **Jev Interpret** or **Jev Skill Choice** to use the same environment variable as **OpenRouter Text**. You can also enter a key directly in each node's `api_key` field. An explicit key takes precedence; an empty field uses the environment variable. A standard Text node can supply the same key to both `api_key` inputs.

To query TypeSafe directly, set **Jev Interpret**'s `provider` to `typesafe` and set `TYPESAFE_API_KEY`. **OpenRouter Text** always uses OpenRouter.

Keys entered directly are saved in workflows and execution history. Remove them before sharing. Restart ComfyUI after setting environment variables.

## OpenRouter Text

Use this node for general text generation or to generate candidates for Jev.

| Input | Description |
| --- | --- |
| `prompt` | Natural-language generation instructions. Accepts connections from standard Text or Format Text nodes. |
| `system` | Optional additional instructions. Can be empty. |
| `output_mode = text` | General text generation. Connect the `text` output to any existing STRING input. |
| `output_mode = candidates` | Generate `count` distinct candidates. Connect `text` to Jev Interpret's `candidates_json`. |
| `model` | Select a text model from the OpenRouter catalog, or use `custom` to enter a model ID. |
| `api_key` | Uses `OPENROUTER_API_KEY` when empty. |
| `refresh` | Keep fixed to reuse results. Change it to regenerate. |
| `temperature` / `max_tokens` | Advanced settings for generation variability and the output token limit. |

The model list comes from [OpenRouter's model catalog](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) when ComfyUI starts. Listing models requires no API key. If fetching fails, the last saved list is used; if no list has been saved, enter an ID with `custom`. Restart ComfyUI to update the list.

Candidate mode requests Structured Outputs internally and returns an array of candidates as a STRING. **Candidates do not need to fit on one line.** Line breaks, paragraphs, and whitespace within each candidate are preserved. A mismatched candidate count, duplicates, empty candidates, or a truncated response produces an explicit error.

Candidate mode requires a model that supports Structured Outputs, including when using `custom`. Regular `text` mode does not request Structured Outputs. The `response_json` output contains the original API response, including the model name and usage.

## Jev Interpret

Put the text to evaluate or your creative intent in `state`, and the judgment instructions in `instructions`. The `choice`, `multi_choice`, `score`, and `suggest` tasks accept candidates in either of these ways:

- **Generated candidates:** Set OpenRouter Text's `output_mode` to `candidates` and connect `text` to `candidates_json`.
- **Existing options:** Connect STRING outputs from standard Text nodes or similar nodes to the Autogrow inputs, starting with `candidates.candidate0`. Each connection is one candidate, including multiline text.

When both are connected, individually connected candidates come first in input-number order, followed by the generated candidates. No candidate names or schemas are required.

| `task` | Judgment and `result` output |
| --- | --- |
| `choice` | Select one candidate and return its complete text unchanged as a STRING. Connect directly to CLIP Text Encode or similar nodes. |
| `multi_choice` | Evaluate each candidate independently and return matching candidates as a JSON array in their original order. |
| `boolean` | Evaluate whether the specified condition is met, returning the string `true` or `false`. No candidates are needed. |
| `score` | Supply at least two candidates as an ordered scale from low to high. Return a numeric string from 0 to 1. |
| `extract` | Select a number from `state` that answers the instructions and return its original text. No candidate inputs are needed. No match produces an error. |
| `suggest` | Rank candidates by description, then evaluate the full content of the shortlist. Return matching candidate values as a JSON array, or `[]` when none fit. |

For `boolean` and `multi_choice`, `threshold` defaults to 0.5 and uses a `>=` comparison. Change it in the advanced settings. Score confidence is not mixed into the evaluation value. Use standard Convert Number nodes when you need a number, or Compare Text and Switch nodes for conditional branching.

Outputs are `result` (STRING), `details` (DICT containing judgment details), and `response_json` (STRING containing the raw API response). You can also pass JSON analysis results to `state` as text. This node does not send images, audio, or video directly to Jev.

The default Jev model is `jev-latest`. TypeSafe also supports `jev-preview`, `jev-1.13.0`, and custom IDs. With OpenRouter, `jev-latest` is sent as `~typesafe/jev-latest` and `jev-1.13.0` as `typesafe/jev-1.13`. OpenRouter does not support `jev-preview`. Select `custom` to specify another ID.

## Image generation examples

Load an `examples/*.workflow.json` file into ComfyUI. A matching `.api.json` file is included for each example. The following three examples are connected through the standard Checkpoint Loader, CLIP Text Encode, Empty Latent Image, KSampler, VAE Decode, and Save Image nodes.

1. **[01_generate_and_select.workflow.json](examples/01_generate_and_select.workflow.json)** — Generate four photographic prompts from a creative brief, let Jev choose one, and generate an image. Start here.
2. **[02_manual_candidates.workflow.json](examples/02_manual_candidates.workflow.json)** — Supply existing multiline prompts through standard Text nodes and let Jev select one. Does not call a text generation API.
3. **[03_select_and_expand.workflow.json](examples/03_select_and_expand.workflow.json)** — Generate and select a photographic concept, then expand it into a detailed image prompt using OpenRouter Text in regular text mode.

Before running, replace `YOUR_SD_OR_SDXL_CHECKPOINT.safetensors` in Checkpoint Loader with an installed SD 1.5 / SDXL checkpoint that includes CLIP and VAE. Set image size, seed, steps, and other generation settings in the standard nodes. The examples do not download models.

## Candidate suggestions

Use `task = suggest` to find procedures or creative directions that help with the request in `state`. Supply the candidate text through `candidates` or `candidates_json` and describe what to look for in `instructions`.

Jev first checks whether specialized guidance is needed, then ranks candidates and evaluates the full text of the shortlist. It returns selected candidates as a JSON array in the STRING `result`. The result is `[]` when guidance is unnecessary or no candidate fits. Connect `result` to a standard Preview as Text node to inspect it. Use `choice` when you want to select one option without this need-for-guidance check.

| Advanced setting | Default | Purpose |
| --- | --- | --- |
| `shortlist_size` | 3 | Number of candidates whose full text is evaluated. |
| `max_selections` | 1 | Maximum number of candidates to return. |
| `gate_threshold` | 0.3 | Minimum assessed need for specialized guidance before evaluating the shortlist. |
| `threshold` | 0.5 | Minimum fit required for each selected candidate. |

To separate a candidate's description from its output, connect a standard Text node containing a JSON array like this to `candidates_json`:

```json
[
  {
    "description": "Soft daylight for a warm, tactile product photograph",
    "content": "Place the bottle on natural linen beside a window. Use diffused side light and keep the background simple.",
    "value": "Amber perfume bottle on natural linen, soft window light, warm neutral tones"
  },
  {
    "description": "Hard studio light for a dramatic, sculptural product photograph",
    "content": "Place the bottle on dark stone. Use a narrow side light to emphasize its silhouette and reflections.",
    "value": "Perfume bottle on dark stone, hard side lighting, deep shadows, sculptural composition"
  }
]
```

Use `description` for the short description, `content` for the full text to evaluate, and `value` for the text or JSON value to return. If `content` is omitted, Jev uses `description`. With the default settings, selected values are returned unchanged inside a JSON array. To evaluate a file's contents, supply the contents as text.

The `choice` and `multi_choice` tasks also accept these records. They compare `description` and return the selected `value` or values.

## Jev Skill Choice

Select installed Claude Skills for a request. Enter the request in `prompt`; the node reads `~/.claude/skills` by default, selects relevant Skills, and outputs their complete contents with application priorities. Change `directory` to use another location.

[04_skill_choice.workflow.json](examples/04_skill_choice.workflow.json) selects from `~/.claude/skills` and shows the selected text and data with standard Preview as Text nodes.

```text
Text → Jev Skill Choice → Preview as Text
          ↑
     ~/.claude/skills
```

| Input | Purpose |
| --- | --- |
| `directory` | Defaults to `~/.claude/skills`. Searches recursively for `SKILL.md`, including linked Skill directories. Relative paths start at the ComfyUI directory. |
| `prompt` | The work for which Skills should be selected. |
| `instructions` | Selection criteria. Defaults to checking usefulness and prerequisites. |
| `max_selections` | Maximum number of selected Skills; default 3. |
| `strength_mode` | `automatic` scores each Skill's role from 0 to 2 and omits zero scores. `uniform` assigns 1 to every selected Skill. |
| `shortlist_size` | Maximum number of Skills whose full contents are evaluated; default 5. |
| `threshold` / `gate_threshold` | Minimum fit and need for specialized guidance; defaults 0.5 and 0.3. |

Set `provider`, `model`, and `api_key` as for Jev Interpret. Skill descriptions and shortlisted file contents are sent to the selected API along with the prompt. The node reads each `SKILL.md`; referenced files and scripts are outside its loading scope.

YAML front matter can provide `name` and `description`. Without them, the directory name and file contents are used. Changes to the files, including additions and removals, trigger a new selection on the next run. Change `refresh` to request another selection with the same inputs.

Outputs are `text` (STRING containing the selected contents and priorities), `skills` (DICT containing a `skills` list of `name`, `path`, `description`, `content`, and `strength` records), `details` (DICT containing selection results), and `response_json` (STRING containing API responses). When no Skill is suitable, `text` is empty and `skills` is `{"skills": []}`.

Connect `text` to OpenRouter Text's `system` input to use the selected guidance for generation, and supply the generation request separately to `prompt`. Priorities express how strongly to apply the guidance; they do not change model weights.

## Caching, errors, and tests

The nodes use ComfyUI's cache. Changing only the image generation seed, width, or height does not trigger another text generation or Jev request. Changing only Jev's judgment instructions can reuse previously generated candidates. Change a node's `refresh` to request a new result from that node on the next run.

Changes to a request node's own inputs, model, key, thresholds, or other settings invalidate its cached result. Each API request has a 60-second timeout. HTTP 429 / 529 responses are retried up to twice according to `Retry-After`. Other failures and malformed responses are returned as errors.

```sh
../../venv/bin/python -m unittest discover -s tests -v
```

The regular tests do not call paid APIs. They use mocked responses to verify candidate generation, selection, and reuse, and run through standard nodes to Save Image using the actual ComfyUI execution engine. Checkpoint loading and trained-model computation are mocked, so these tests do not evaluate image quality or real API judgment accuracy.

API references: [TypeSafe](https://docs.typesafe.ai/introduction) / [OpenRouter Chat Completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion) / [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
