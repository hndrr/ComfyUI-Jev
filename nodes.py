from comfy_api.latest import ComfyExtension, io
import folder_paths

from . import api, boxes, decisions, media, model_catalog, reference, semantics as s, skills, suggestions


def jev_connection_inputs():
    return [
        io.DynamicCombo.Input("model", options=[
            *[io.DynamicCombo.Option(model_id, []) for model_id in model_catalog.TYPESAFE_MODELS],
            *[io.DynamicCombo.Option(model_id, []) for model_id in sorted(model_catalog.decision_models)],
            io.DynamicCombo.Option("custom", [io.String.Input("model_id", default="jev-1.13.0")]),
        ], tooltip="OpenRouter lists only Decisions models from its public catalog, refreshed at startup. Saved model choices are preserved.",
            extra_dict={"jev_provider_models": {
                "typesafe": [*model_catalog.TYPESAFE_MODELS, "custom"],
                "openrouter": list(model_catalog.decision_model_ids),
            }}),
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


def context_inputs():
    return [
        io.Image.Input("images", optional=True, tooltip="Entire IMAGE batch, in order. TypeSafe: still PNG/JPEG, 280–1568 pixels per side. OpenRouter requires an image-capable Decisions model. Known limits: Luna 128, Clef 4."),
        io.String.Input("content_json", force_input=True, optional=True,
                        tooltip="JSON array of text and image_url parts. Images must be PNG/JPEG/WebP data URLs. Order: state/prompt, content_json, IMAGE batch."),
    ]


class JevInterpret(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevInterpret", display_name="Jev Interpret", category="Jev",
            description="Judge text and optional image context using instructions and plain-text candidates. The selected candidate connects directly to existing text inputs.",
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
                *context_inputs(),
            ],
            outputs=[io.String.Output(display_name="result"), io.Dict.Output(display_name="details"), io.String.Output(display_name="response_json")],
        )

    @classmethod
    async def execute(cls, state, instructions, task, model, provider="typesafe", api_key="", refresh=0,
                      threshold=0.5, candidates=None, candidates_json=None, shortlist_size=3, max_selections=1,
                      gate_threshold=0.3, images=None, content_json=None):
        model_id = model["model_id"] if model["model"] == "custom" else model["model"]
        if not model_id.strip():
            raise ValueError("Model ID cannot be empty")
        context = media.prepare(state, content_json, images)
        if task == "suggest":
            candidates = s.text_candidates(candidates, candidates_json)
            values, details, responses = await suggestions.suggest(
                context, instructions, candidates, model_id, provider, api_key,
                shortlist_size, max_selections, gate_threshold, threshold,
            )
            return io.NodeOutput(s.dumps(values), details, s.dumps(responses))
        schema, bindings = s.text_task(instructions, {
            "task": task, "candidates": candidates or {}, "candidates_json": candidates_json,
        }, threshold)
        questions, plans = s.compile_questions(state, schema)
        response = await api.evaluate(context, questions, model_id, provider=provider, api_key=api_key)
        judgments = s.collect_judgments(response, plans)
        resolved, values = s.resolve(judgments, bindings)
        if "value" not in values:
            raise ValueError("No source value answers the extraction instructions")
        value = values["value"]
        result = value if isinstance(value, str) else s.dumps(value)
        details = {**resolved["fields"]["value"], "model": response.get("model"), "usage": response.get("usage", {})}
        notices = decisions.context_warnings(context, model_id, provider)
        if notices:
            details["warnings"] = notices
        return io.NodeOutput(result, details, s.dumps(response))


class JevSkillChoice(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevSkillChoice", display_name="Jev Skill Choice", category="Jev",
            description="Select guidance for a prompt and optional image context from installed skills. Discovers shared agent and Claude skill directories. Outputs selected contents and priorities.",
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
                *context_inputs(),
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
                      max_selections=3, strength_mode="automatic", shortlist_size=5, threshold=0.5, gate_threshold=0.3,
                      images=None, content_json=None):
        model_id = model["model_id"] if model["model"] == "custom" else model["model"]
        if not model_id.strip():
            raise ValueError("Model ID cannot be empty")
        context = media.prepare(prompt, content_json, images)
        if strength_mode not in ("automatic", "uniform"):
            raise ValueError("strength_mode must be automatic or uniform")
        records = skills.read_skills(directory, folder_paths.base_path)
        candidates = [{"description": f"{item['name']}\n{item['description']}", "content": item["content"],
                       "value": item["path"]} for item in records]
        automatic = strength_mode == "automatic"
        values, details, responses = await suggestions.suggest(
            context, instructions, candidates, model_id, provider, api_key,
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


class JevFindBox(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevFindBox", display_name="Jev Find Box", category="Jev",
            description="Find one described target in a selected image with Jev 1.14 through TypeSafe or OpenRouter. Always checks presence in the same request. Experimental bounding rectangle, not segmentation.",
            inputs=[
                io.Image.Input("images"),
                io.String.Input("target", multiline=True, default="the mug nearest the sink",
                                tooltip="Describe one distinguishable target. One rectangle is returned; this does not detect every instance."),
                io.Int.Input("image_index", default=0, min=0, tooltip="Zero-based frame in an IMAGE batch. Only this frame is uploaded and processed."),
                io.Combo.Input("model", options=[*decisions.BOX_MODELS, *decisions.TYPESAFE_BOX_MODELS], default=decisions.BOX_MODELS[0],
                               extra_dict={"jev_provider_models": {"openrouter": list(decisions.BOX_MODELS), "typesafe": list(decisions.TYPESAFE_BOX_MODELS)}}),
                io.String.Input("api_key", default="", tooltip="Empty uses TYPESAFE_API_KEY or OPENROUTER_API_KEY for the selected provider. Entered keys are saved in workflows; remove before sharing."),
                io.Int.Input("refresh", default=0, min=0, control_after_generate=io.ControlAfterGenerate.fixed),
                io.Float.Input("presence_threshold", default=0.8, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Minimum Noul yes probability. An adjustable starting point, not a validated accuracy guarantee."),
                io.Float.Input("min_confidence", default=0.5, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Minimum Box confidence, in addition to presence. Tune on your images and inspect the preview."),
                io.Combo.Input("provider", options=["openrouter", "typesafe"], default="openrouter", optional=True,
                               tooltip="OpenRouter remains the default for saved workflows. TypeSafe direct requires its own TYPESAFE_API_KEY."),
            ],
            outputs=[io.Dict.Output(display_name="box_data"), io.Boolean.Output(display_name="found"),
                     io.Float.Output(display_name="confidence")],
        )

    @classmethod
    async def execute(cls, images, target, image_index=0, model=decisions.BOX_MODELS[0], api_key="", refresh=0,
                      presence_threshold=0.8, min_confidence=0.5, provider="openrouter"):
        if provider not in ("openrouter", "typesafe"):
            raise ValueError("Select provider openrouter or typesafe")
        supported = decisions.TYPESAFE_BOX_MODELS if provider == "typesafe" else decisions.BOX_MODELS
        if model not in supported:
            raise ValueError("Select a supported Jev 1.14 Box model for the chosen provider")
        questions = s.box_questions(target)
        s._probability(presence_threshold, "presence_threshold")
        s._probability(min_confidence, "min_confidence")
        frame = boxes.selected_image(images, image_index)
        source_hash = boxes.image_fingerprint(frame)
        context, upload_size = boxes.context(frame, provider)
        response = await api.evaluate(context, questions, model, provider=provider, api_key=api_key)
        result = s.collect_box(response, presence_threshold, min_confidence)
        result.update({"image_index": image_index, "batch_size": images.shape[0],
                       "source_hash": source_hash,
                       "width": images.shape[2], "height": images.shape[1], "upload_size": upload_size,
                       "target": target, "model": response.get("model"), "usage": response.get("usage", {})})
        result["provider"] = provider
        return io.NodeOutput(result, result["found"], result["confidence"])


class JevBoxRegion(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevBoxRegion", display_name="Jev Box Region", category="Jev",
            description="Create a crop, full-resolution rectangular mask, and separate outline preview locally. Connect the same images as Jev Find Box. Padding changes reuse the API result.",
            inputs=[
                io.Image.Input("images"), io.Dict.Input("box_data"),
                io.Int.Input("padding", default=0, min=0, tooltip="Original-image pixels added on each side, clipped to the image."),
                io.Combo.Input("on_missing", options=["error", "keep_image"], default="error",
                               tooltip="error stops before cropping a rejected result. keep_image explicitly returns the selected image, empty mask, and found=false in details."),
            ],
            outputs=[io.Image.Output(display_name="crop"), io.Mask.Output(display_name="mask"),
                     io.Image.Output(display_name="preview"), io.Dict.Output(display_name="details")],
        )

    @classmethod
    def execute(cls, images, box_data, padding=0, on_missing="error"):
        return io.NodeOutput(*boxes.region(images, box_data, padding, on_missing))


class JevReferenceEvaluate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        choices = reference.model_choices()
        return io.Schema(
            node_id="JevReferenceEvaluate", display_name="Jev Reference Evaluate", category="Jev",
            description="Evaluate one selected image against one separately selected reference. Jev only. The evaluation image is state; the reference is in the question/rubric. No batch pairing or image generation.",
            inputs=[
                io.Image.Input("images", tooltip="Image batch to evaluate. Only image_index is sent."),
                io.Image.Input("reference_images", tooltip="Separate reference batch. Only reference_index is sent; it is never implicitly paired with the target batch."),
                io.String.Input("instructions", multiline=True, default="Does the evaluation image show the same product as the reference? Ignore background changes."),
                io.Combo.Input("task", options=["match", "score"], default="match",
                               tooltip="match returns Noul match probability. score returns a normalized 3-level rubric score, with separate confidence in details."),
                io.Int.Input("image_index", default=0, min=0),
                io.Int.Input("reference_index", default=0, min=0),
                io.Combo.Input("model", options=[*choices["openrouter"], *choices["typesafe"]], default="typesafe/jev-1.14",
                               extra_dict={"jev_provider_models": choices}),
                io.String.Input("api_key", default="", tooltip="Empty uses the selected provider's environment key. Keys entered here are saved with workflows."),
                io.Int.Input("refresh", default=0, min=0, control_after_generate=io.ControlAfterGenerate.fixed),
                io.Combo.Input("provider", options=["openrouter", "typesafe"], default="openrouter"),
                io.Combo.Input("reference_placement", options=["instructions", "criteria"], default="instructions", optional=True, advanced=True,
                               tooltip="instructions: the reference illustrates the task. criteria: the reference illustrates the positive/highest rubric level."),
                io.Float.Input("threshold", default=0.8, min=0, max=1, step=0.01, optional=True, advanced=True),
                io.Float.Input("min_confidence", default=0.5, min=0, max=1, step=0.01, optional=True, advanced=True,
                               tooltip="Score task only. Noul has no separate confidence; its match probability is the value."),
            ], outputs=[io.Float.Output(display_name="value"), io.Boolean.Output(display_name="accepted"),
                        io.Dict.Output(display_name="details"), io.String.Output(display_name="response_json")],
        )

    @classmethod
    async def execute(cls, images, reference_images, instructions, task="match", image_index=0, reference_index=0,
                      model="typesafe/jev-1.14", api_key="", refresh=0, provider="openrouter",
                      reference_placement="instructions", threshold=0.8, min_confidence=0.5):
        s._probability(threshold, "threshold")
        s._probability(min_confidence, "min_confidence")
        state, questions, metadata = reference.prepare(images, reference_images, instructions, task,
            image_index, reference_index, reference_placement, model, provider)
        response = await api.evaluate(state, questions, model, provider=provider, api_key=api_key)
        value, accepted, details = reference.collect(response, questions["evaluation"], task, threshold,
            min_confidence, metadata, reference_placement, provider)
        return io.NodeOutput(value, accepted, details, s.dumps(response))


NODE_CLASSES = [JevInterpret, OpenRouterText, JevSkillChoice, JevFindBox, JevBoxRegion, JevReferenceEvaluate]


class JevExtension(ComfyExtension):
    async def get_node_list(self):
        return NODE_CLASSES
