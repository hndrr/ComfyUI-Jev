from comfy_api.latest import ComfyExtension, io
import folder_paths

from . import api, model_catalog, semantics as s, skills, suggestions


def jev_connection_inputs():
    return [
        io.DynamicCombo.Input("model", options=[
            io.DynamicCombo.Option("jev-latest", []),
            io.DynamicCombo.Option("jev-preview", []),
            io.DynamicCombo.Option("jev-1.13.0", []),
            io.DynamicCombo.Option("custom", [io.String.Input("model_id", default="jev-1.13.0")]),
        ]),
        io.Combo.Input("provider", options=["typesafe", "openrouter"], default="typesafe"),
        io.String.Input("api_key", default="", tooltip="Empty uses the provider's environment variable. Entered keys are saved in workflows; remove before sharing."),
        io.Int.Input("refresh", default=0, min=0, control_after_generate=io.ControlAfterGenerate.fixed, tooltip="Keep fixed to reuse results. Change to request a new judgment."),
    ]


def candidate_inputs():
    return [
        io.Autogrow.Input("candidates", io.Autogrow.TemplatePrefix(
            io.String.Input("candidate", force_input=True), prefix="candidate", min=0, max=100)),
        io.String.Input("candidates_json", force_input=True, optional=True,
                        tooltip="Connect OpenRouter Text in candidates mode, or supply a JSON array of text strings or description/content/value records."),
    ]


class JevInterpret(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevInterpret", display_name="Jev Interpret", category="Jev",
            description="Judge text using instructions and plain-text candidates. The selected candidate can connect directly to existing text inputs. No schema JSON required.",
            inputs=[
                io.String.Input("state", multiline=True, default="I want a sense of luxury that feels warm and approachable."),
                io.String.Input("instructions", multiline=True, default="Choose the candidate that best meets the creative intent."),
                io.Combo.Input("task", options=[*s.FIELD_TYPES, "suggest"], default="choice"),
                *candidate_inputs(),
                *jev_connection_inputs(),
                io.Float.Input("threshold", default=0.5, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Minimum yes probability for boolean/multi_choice or candidate fit in suggest mode."),
                io.Int.Input("shortlist_size", default=3, min=1, optional=True, advanced=True,
                             tooltip="Suggest mode: how many candidates receive full-content verification."),
                io.Int.Input("max_selections", default=1, min=1, optional=True, advanced=True,
                             tooltip="Suggest mode: maximum accepted candidates. May return none."),
                io.Float.Input("gate_threshold", default=0.3, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Suggest mode: minimum need for specialized guidance before full-content verification."),
            ],
            outputs=[io.String.Output(display_name="result"), io.Dict.Output(display_name="details"), io.String.Output(display_name="response_json")],
        )

    @classmethod
    async def execute(cls, state, instructions, task, model, provider="typesafe", api_key="", refresh=0,
                      threshold=0.5, candidates=None, candidates_json=None, shortlist_size=3, max_selections=1,
                      gate_threshold=0.3):
        model_id = model["model_id"] if model["model"] == "custom" else model["model"]
        if not model_id.strip():
            raise ValueError("Model ID cannot be empty")
        if task == "suggest":
            candidates = s.text_candidates(candidates, candidates_json)
            values, details, responses = await suggestions.suggest(
                state, instructions, candidates, model_id, provider, api_key,
                shortlist_size, max_selections, gate_threshold, threshold,
            )
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


class JevSkillChoice(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevSkillChoice", display_name="Jev Skill Choice", category="Jev",
            description="Select guidance for a prompt from installed skills. Discovers shared agent and Claude skill directories. Outputs selected contents and priorities.",
            inputs=[
                io.String.Input("prompt", multiline=True, default="Write a detailed image prompt for a warm, approachable perfume advertisement."),
                io.DynamicCombo.Input("directory", options=[
                    io.DynamicCombo.Option("automatic", []),
                    *[io.DynamicCombo.Option(str(root), []) for root in skills.skill_directories(folder_paths.base_path)],
                    io.DynamicCombo.Option("custom", [io.String.Input("path", default="",
                        tooltip="Skill directory. Relative paths use the ComfyUI directory; ~ is supported.")]),
                ], tooltip="Select a discovered skill directory, automatic to use all discovered directories, or custom to enter a path. Discovers shared agent and Claude skills."),
                io.String.Input("instructions", multiline=True, default="Select skills that directly help with the requested work. Check each skill's purpose and prerequisites."),
                *jev_connection_inputs(),
                io.Int.Input("max_selections", default=3, min=1, tooltip="Maximum number of skills to return. May return none."),
                io.Combo.Input("strength_mode", options=["automatic", "uniform"], default="automatic",
                               tooltip="Automatic scores each selected skill's role from 0 to 2. Uniform assigns 1 to every selected skill. These are guidance priorities."),
                io.Int.Input("shortlist_size", default=5, min=1, optional=True, advanced=True),
                io.Float.Input("threshold", default=0.5, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Minimum fit required for each selected skill."),
                io.Float.Input("gate_threshold", default=0.3, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Minimum need for specialized guidance before evaluating full skill contents."),
            ],
            outputs=[io.String.Output(display_name="text"), io.Dict.Output(display_name="skills"),
                     io.Dict.Output(display_name="details"), io.String.Output(display_name="response_json")],
        )

    @classmethod
    def fingerprint_inputs(cls, directory, **kwargs):
        if directory is None:
            return None
        return skills.fingerprint(directory, folder_paths.base_path)

    @classmethod
    async def execute(cls, prompt, directory, instructions, model, provider="typesafe", api_key="", refresh=0,
                      max_selections=3, strength_mode="automatic", shortlist_size=5, threshold=0.5, gate_threshold=0.3):
        model_id = model["model_id"] if model["model"] == "custom" else model["model"]
        if not model_id.strip():
            raise ValueError("Model ID cannot be empty")
        if strength_mode not in ("automatic", "uniform"):
            raise ValueError("strength_mode must be automatic or uniform")
        records = skills.read_skills(directory, folder_paths.base_path)
        candidates = [{"description": f"{item['name']}\n{item['description']}", "content": item["content"],
                       "value": item["path"]} for item in records]
        automatic = strength_mode == "automatic"
        values, details, responses = await suggestions.suggest(
            prompt, instructions, candidates, model_id, provider, api_key,
            shortlist_size, max_selections, gate_threshold, threshold, score_applicability=automatic,
        )
        by_path = {item["path"]: item for item in records}
        selected = [{**by_path[path], "strength": 2 * details["applicability"][key] if automatic else 1.0}
                    for key, path in zip(details["selected"], values)]
        details["strengths"] = {key: item["strength"] for key, item in zip(details["selected"], selected)}
        return io.NodeOutput(skills.render_skills(selected), {"skills": selected}, details, s.dumps(responses))


class OpenRouterText(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="OpenRouterText", display_name="OpenRouter Text", category="Jev",
            description="Generate text with OpenRouter. Candidates mode generates complete alternatives for Jev Interpret without writing a JSON schema.",
            inputs=[
                io.String.Input("prompt", multiline=True, default="Suggest distinct photographic concepts for a perfume advertisement as English image generation prompts."),
                io.String.Input("system", multiline=True, default="", tooltip="Optional instructions for the text model."),
                io.DynamicCombo.Input("output_mode", options=[
                    io.DynamicCombo.Option("text", []),
                    io.DynamicCombo.Option("candidates", [
                        io.Int.Input("count", default=4, min=2, max=100),
                    ]),
                ]),
                io.DynamicCombo.Input("model", options=[
                    *[io.DynamicCombo.Option(model_id, []) for model_id in model_catalog.model_ids],
                    io.DynamicCombo.Option("custom", [io.String.Input("model_id", default="")]),
                ], tooltip="Text models from the OpenRouter catalog, loaded when ComfyUI starts. Select custom to enter a model ID."),
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


NODE_CLASSES = [JevInterpret, OpenRouterText, JevSkillChoice]


class JevExtension(ComfyExtension):
    async def get_node_list(self):
        return NODE_CLASSES
