# Node reference

[Installation and first workflow](../README.md) | [日本語](nodes.ja.md)

Inputs, outputs, and advanced settings for each node. For API key setup, see the [README](../README.md#api-key-setup).

## Jev Interpret

Put the text or situation to evaluate in `state`, and the judgment instructions in `instructions`. The `choice`, `multi_choice`, `score`, and `suggest` tasks accept candidates in either of these ways:

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

### Candidate suggestions

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

## Jev Skill Choice

Select installed Skills for a request. Enter the request in `prompt`; the node selects relevant Skills and outputs their complete contents with application priorities. It discovers shared agent Skills in `.agents/skills` and Claude Skills in `.claude/skills`, under both the user home and the ComfyUI project directory. If `CLAUDE_CONFIG_DIR` is set, it changes only the Claude user location. Linked copies of the same Skill file are loaded once.

[04_skill_choice.workflow.json](../examples/04_skill_choice.workflow.json) discovers installed Skills and shows the selected text and data with standard Preview as Text nodes.

```text
Text → Jev Skill Choice → Preview as Text
          ↑
     Installed Skills
```

| Input | Purpose |
| --- | --- |
| `directory` | Select a discovered Skill directory from the dropdown. `automatic` uses all discovered directories; `custom` shows a field for an arbitrary path. Searches recursively for `SKILL.md`, including linked Skill directories. Relative paths start at the ComfyUI directory. |
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

## Execution and caching

The nodes use ComfyUI's cache. Changing only the image generation seed, width, or height does not trigger another text generation or Jev request. Changing only Jev's judgment instructions can reuse previously generated candidates. Change a node's `refresh` to request a new result from that node on the next run.

Changes to a request node's own inputs, model, key, thresholds, or other settings invalidate its cached result. Each API request has a 60-second timeout. HTTP 429 / 529 responses are retried up to twice according to `Retry-After`. Other failures and malformed responses are returned as errors.
