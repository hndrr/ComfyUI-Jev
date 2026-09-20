from comfy_api.latest import ComfyExtension, io

from . import api, semantics as s, suggestions


def candidate_inputs():
    return [
        io.Autogrow.Input("candidates", io.Autogrow.TemplatePrefix(
            io.String.Input("candidate", force_input=True), prefix="candidate", min=0, max=100)),
        io.String.Input("candidates_json", force_input=True, optional=True,
                        tooltip="Connect OpenRouter Text in candidates mode or Skill Catalog. Supports complete strings or description/value/content records."),
    ]


class JevInterpret(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevInterpret", display_name="Jev Interpret", category="Jev",
            description="Judge text using instructions and plain-text candidates. The selected candidate can connect directly to existing text inputs. No schema JSON required.",
            inputs=[
                io.String.Input("state", multiline=True, default="高級感は欲しいが、冷たく威圧的にはしたくない。"),
                io.String.Input("instructions", multiline=True, default="制作意図を最も満たす候補を選ぶ。"),
                io.Combo.Input("task", options=[*s.FIELD_TYPES, "suggest"], default="choice"),
                *candidate_inputs(),
                io.DynamicCombo.Input("model", options=[
                    io.DynamicCombo.Option("jev-latest", []),
                    io.DynamicCombo.Option("jev-preview", []),
                    io.DynamicCombo.Option("jev-1.13.0", []),
                    io.DynamicCombo.Option("custom", [io.String.Input("model_id", default="jev-1.13.0")]),
                ]),
                io.Combo.Input("provider", options=["typesafe", "openrouter"], default="typesafe"),
                io.String.Input("api_key", default="", tooltip="Empty uses the provider's environment variable. Entered keys are saved in workflows; remove before sharing."),
                io.Int.Input("refresh", default=0, min=0, control_after_generate=io.ControlAfterGenerate.fixed, tooltip="Keep fixed to reuse results. Change to request a new judgment."),
                io.Float.Input("threshold", default=0.5, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Minimum yes probability for boolean/multi_choice or candidate fit in suggest mode."),
                io.Int.Input("shortlist_size", default=3, min=1, optional=True, advanced=True,
                             tooltip="Suggest mode: how many candidates receive full-content verification."),
                io.Int.Input("max_selections", default=1, min=1, optional=True, advanced=True,
                             tooltip="Suggest mode: maximum accepted candidates. May return none."),
                io.Float.Input("gate_threshold", default=0.3, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Suggest mode: minimum need for specialized guidance before full-content verification."),
                io.Combo.Input("skill_strength", options=["preserve", "automatic"], default="preserve", optional=True, advanced=True,
                               tooltip="Suggest mode with Skill Catalog: automatic scores each shortlisted skill's role and writes strength from 0 (omit) to 2 (central). Preserve keeps supplied strengths. These are agent instructions, not model weights."),
            ],
            outputs=[io.String.Output(display_name="result"), io.Dict.Output(display_name="details"), io.String.Output(display_name="response_json")],
        )

    @classmethod
    async def execute(cls, state, instructions, task, model, provider="typesafe", api_key="", refresh=0,
                      threshold=0.5, candidates=None, candidates_json=None, shortlist_size=3, max_selections=1,
                      gate_threshold=0.3, skill_strength="preserve"):
        model_id = model["model_id"] if model["model"] == "custom" else model["model"]
        if not model_id.strip():
            raise ValueError("Model ID cannot be empty")
        if task == "suggest":
            candidates = s.text_candidates(candidates, candidates_json)
            if skill_strength not in ("preserve", "automatic"):
                raise ValueError("skill_strength must be preserve or automatic")
            automatic = skill_strength == "automatic"
            if automatic:
                for index, candidate in enumerate(candidates):
                    value = candidate["value"]
                    if not isinstance(value, dict) or not isinstance(value.get("skill_path"), str) or not value["skill_path"].strip():
                        raise ValueError(f"candidate c{index}: automatic skill_strength requires a Skill Catalog value with skill_path")
            values, details, responses = await suggestions.suggest(
                state, instructions, candidates, model_id, provider, api_key,
                shortlist_size, max_selections, gate_threshold, threshold, score_applicability=automatic,
            )
            if automatic:
                details["strengths"] = {key: 2 * details["applicability"][key] for key in details["selected"]}
                values = [{**value, "strength": details["strengths"][key]} for key, value in zip(details["selected"], values)]
            return io.NodeOutput(s.dumps(values), details, s.dumps(responses))
        schema, bindings = s.text_task(instructions, {
            "task": task, "candidates": candidates or {}, "candidates_json": candidates_json,
        }, threshold)
        questions, plans = s.compile_questions(state, schema)
        response = await api.evaluate(state, questions, model_id, provider=provider, api_key=api_key)
        judgments = s.collect_judgments(response, plans)
        resolved, values = s.resolve(judgments, bindings)
        if "value" not in values:
            raise ValueError("No source value answers the extraction instructions")
        value = values["value"]
        result = value if isinstance(value, str) else s.dumps(value)
        details = {**resolved["fields"]["value"], "model": response.get("model"), "usage": response.get("usage", {})}
        return io.NodeOutput(result, details, s.dumps(response))


class OpenRouterText(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="OpenRouterText", display_name="OpenRouter Text", category="Jev",
            description="Generate text with OpenRouter. Candidates mode generates complete alternatives for Jev Interpret without writing a JSON schema.",
            inputs=[
                io.String.Input("prompt", multiline=True, default="香水の広告写真に使う、異なる撮影案を英語の画像生成プロンプトで提案してください。"),
                io.String.Input("system", multiline=True, default="", tooltip="Optional instructions for the text model."),
                io.DynamicCombo.Input("output_mode", options=[
                    io.DynamicCombo.Option("text", []),
                    io.DynamicCombo.Option("candidates", [
                        io.Int.Input("count", default=4, min=2, max=100),
                    ]),
                ]),
                io.DynamicCombo.Input("model", options=[
                    io.DynamicCombo.Option("openai/gpt-4.1-mini", []),
                    io.DynamicCombo.Option("openai/gpt-4.1", []),
                    io.DynamicCombo.Option("custom", [io.String.Input("model_id", default="openai/gpt-4.1-mini")]),
                ]),
                io.String.Input("api_key", default="", tooltip="Empty uses OPENROUTER_API_KEY, shared with Jev Interpret using OpenRouter. Entered keys are saved in workflows."),
                io.Int.Input("refresh", default=0, min=0, control_after_generate=io.ControlAfterGenerate.fixed),
                io.Float.Input("temperature", default=0.7, min=0, max=2, step=0.01, optional=True, advanced=True),
                io.Int.Input("max_tokens", default=2048, min=1, max=131072, optional=True, advanced=True),
            ],
            outputs=[io.String.Output(display_name="text"), io.String.Output(display_name="response_json")],
        )

    @classmethod
    async def execute(cls, prompt, system, output_mode, model, api_key="", refresh=0, temperature=0.7, max_tokens=2048):
        mode = output_mode.get("output_mode")
        if mode not in ("text", "candidates"):
            raise ValueError("OpenRouter Text: output_mode must be text or candidates")
        count = output_mode.get("count", 4) if mode == "candidates" else 0
        model_id = model["model_id"] if model["model"] == "custom" else model["model"]
        response = await api.generate(prompt, system, model_id, temperature, max_tokens, count, api_key)
        return io.NodeOutput(api.generated_text(response, count), s.dumps(response))


NODE_CLASSES = [JevInterpret, OpenRouterText]


class JevExtension(ComfyExtension):
    async def get_node_list(self):
        return NODE_CLASSES
