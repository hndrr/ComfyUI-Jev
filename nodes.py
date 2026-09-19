import fnmatch

import folder_paths
from comfy_api.latest import ComfyExtension, io

from . import api, semantics as s


SchemaType = io.Custom("JEV_SCHEMA")
JudgmentsType = io.Custom("JEV_JUDGMENTS")
ResultType = io.Custom("JEV_RESULT")
MODEL_CATEGORIES = ("checkpoints", "diffusion_models", "loras")


class _ComboType(str):
    def __ne__(self, other):
        # ComfyUI 0.36 validates legacy loader combos against their option lists.
        return False if isinstance(other, list) else super().__ne__(other)


ComboType = io.Custom(_ComboType("COMBO"))


def criteria_inputs(default):
    return [io.Combo.Input("format", options=["lines", "json"], default="lines"),
            io.String.Input("criteria", multiline=True, default=default)]


class JevField(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevField", display_name="Jev Field", category="Jev/schema",
            description="Define one semantic field. Connect several fields to Jev Interpret. Values are mapped later in Jev Resolve.",
            inputs=[
                io.String.Input("field_id", default="style"),
                io.String.Input("instructions", multiline=True, default="Which style best matches the request?"),
                io.Combo.Input("presence", options=["infer", "explicit"], default="infer",
                               tooltip="infer: interpret context. explicit: require information explicitly stated in the input."),
                io.DynamicCombo.Input("kind", options=[
                    io.DynamicCombo.Option("choice", criteria_inputs("natural\ngraphic")),
                    io.DynamicCombo.Option("multi_choice", criteria_inputs("natural\ngraphic")),
                    io.DynamicCombo.Option("boolean", [io.String.Input("criteria", multiline=True, default="", tooltip='Optional JSON: {"true": "...", "false": "..."}')]),
                    io.DynamicCombo.Option("score", criteria_inputs("Minimal\nModerate\nStrong")),
                    io.DynamicCombo.Option("extract", [
                        io.String.Input("source", default="", tooltip="JSON Pointer to source text; empty selects the entire text input."),
                        io.DynamicCombo.Input("extractor", options=[
                            io.DynamicCombo.Option("number", []),
                            io.DynamicCombo.Option("regex", [io.String.Input("pattern", default=r"\S+", multiline=True), io.Int.Input("group", default=0, min=0)]),
                        ]),
                    ]),
                ]),
            ],
            outputs=[SchemaType.Output(display_name="schema"), io.String.Output(display_name="schema_json")],
        )

    @classmethod
    def execute(cls, field_id, instructions, presence, kind):
        field_kind = kind["kind"]
        extractor = kind.get("extractor", {"extractor": "number"})
        schema = s.make_field(field_id, instructions, field_kind, presence,
                              kind.get("criteria", ""), kind.get("format", "lines"), kind.get("source", ""),
                              extractor["pattern"] if extractor["extractor"] == "regex" else s.NUMBER_PATTERN,
                              extractor.get("group", 0))
        return io.NodeOutput(schema, s.dumps(schema))


class JevInterpret(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevInterpret", display_name="Jev Interpret", category="Jev",
            description="Send state and independent semantic fields to TypeSafe or OpenRouter. Enter api_key or set TYPESAFE_API_KEY / OPENROUTER_API_KEY. Reuses ComfyUI's cache; change refresh to request new answers.",
            inputs=[
                io.String.Input("state", multiline=True, default="縦長、動きは控えめ、8秒"),
                io.Combo.Input("state_format", options=["text", "json"], default="text"),
                io.Autogrow.Input("schemas", io.Autogrow.TemplatePrefix(SchemaType.Input("schema"), prefix="schema", min=0, max=100)),
                io.String.Input("schema_json", multiline=True, default="", tooltip="Optional schema object. Merged with connected fields; duplicate IDs are errors."),
                io.DynamicCombo.Input("model", options=[
                    io.DynamicCombo.Option("jev-latest", []), io.DynamicCombo.Option("jev-preview", []),
                    io.DynamicCombo.Option("jev-1.13.0", []),
                    io.DynamicCombo.Option("custom", [io.String.Input("model_id", default="jev-1.13.0")]),
                ]),
                io.Int.Input("refresh", default=0, min=0, control_after_generate=io.ControlAfterGenerate.fixed, tooltip="Keep fixed to reuse results. Increment to request new answers; not an API seed."),
                io.Combo.Input("provider", options=["typesafe", "openrouter"], default="typesafe", optional=True,
                               tooltip="OpenRouter uses OPENROUTER_API_KEY and its Decisions API. Latest maps to ~typesafe/jev-latest; custom accepts an exact model ID."),
                io.String.Input("api_key", default="", optional=True,
                                tooltip="Key for the selected provider. Empty uses its environment variable. Entered keys are saved in workflows; remove before sharing."),
            ],
            outputs=[JudgmentsType.Output(display_name="judgments"), io.String.Output(display_name="response_json")],
        )

    @classmethod
    async def execute(cls, state, state_format, model, refresh, schemas=None, schema_json="", provider="typesafe", api_key=""):
        if state_format not in ("text", "json"):
            raise ValueError("state_format must be text or json")
        state = s.loads(state, "State") if state_format == "json" else state
        schema = s.merge_schemas((schemas or {}).values(), schema_json)
        questions, plans = s.compile_questions(state, schema)
        model_id = model["model_id"] if model["model"] == "custom" else model["model"]
        if not model_id.strip():
            raise ValueError("Model ID cannot be empty")
        response = await api.evaluate(state, questions, model_id, provider=provider, api_key=api_key)
        return io.NodeOutput(s.collect_judgments(response, plans), s.dumps(response))


class JevResolve(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="JevResolve", display_name="Jev Resolve", category="Jev",
            description="Map judgments into production values locally. Changing bindings, thresholds or defaults does not call Jev again.",
            inputs=[JudgmentsType.Input("judgments"), io.String.Input("bindings_json", multiline=True, default="{}")],
            outputs=[ResultType.Output(display_name="result"), io.Dict.Output(display_name="values"), io.String.Output(display_name="values_json")],
        )

    @classmethod
    def execute(cls, judgments, bindings_json):
        result, values = s.resolve(judgments, s.object_json(bindings_json, "Bindings"))
        return io.NodeOutput(result, values, s.dumps(values))


def reader_schema(node_id, display_name, outputs):
    return io.Schema(node_id=node_id, display_name=display_name, category="Jev/values",
                     inputs=[ResultType.Input("result"), io.String.Input("field_id", default="style"),
                             io.String.Input("pointer", default="", tooltip="Optional JSON Pointer inside the field value, e.g. /width or /0.")],
                     outputs=outputs)


class JevReadString(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return reader_schema("JevReadString", "Jev Read String", [io.String.Output(display_name="string"), ComboType.Output(display_name="combo")])

    @classmethod
    def execute(cls, result, field_id, pointer=""):
        value = s.read_value(result, field_id, pointer, str)
        return io.NodeOutput(value, value)


class JevReadInt(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return reader_schema("JevReadInt", "Jev Read Int", [io.Int.Output(display_name="integer")])

    @classmethod
    def execute(cls, result, field_id, pointer=""):
        return io.NodeOutput(s.read_value(result, field_id, pointer, int))


class JevReadFloat(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return reader_schema("JevReadFloat", "Jev Read Float", [io.Float.Output(display_name="float")])

    @classmethod
    def execute(cls, result, field_id, pointer=""):
        return io.NodeOutput(s.read_value(result, field_id, pointer, float))


class JevReadBoolean(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return reader_schema("JevReadBoolean", "Jev Read Boolean", [io.Boolean.Output(display_name="boolean")])

    @classmethod
    def execute(cls, result, field_id, pointer=""):
        return io.NodeOutput(s.read_value(result, field_id, pointer, bool))


class JevInspectField(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="JevInspectField", display_name="Jev Inspect Field", category="Jev/values",
                         inputs=[ResultType.Input("result"), io.String.Input("field_id", default="style")],
                         outputs=[io.Boolean.Output(display_name="has_value"), io.String.Output(display_name="details_json")])

    @classmethod
    def execute(cls, result, field_id):
        if field_id not in result["fields"]:
            raise ValueError(f"Unknown field: {field_id}")
        field = result["fields"][field_id]
        return io.NodeOutput(field["status"] != "unresolved", s.dumps(field))


class JevModelCandidates(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="JevModelCandidates", display_name="Jev Model Candidates", category="Jev/schema",
                         description="Read local model filenames, without loading weights. Connect to a choice/multi_choice field's criteria and select JSON format. Filter to compatible models.",
                         inputs=[io.Combo.Input("category", options=list(MODEL_CATEGORIES)), io.String.Input("pattern", default="*"),
                                 io.String.Input("descriptions_json", multiline=True, default="{}")],
                         outputs=[io.String.Output(display_name="candidates_json")])

    @classmethod
    def filenames(cls, category, pattern):
        if category not in MODEL_CATEGORIES:
            raise ValueError(f"Unsupported model category: {category}")
        return [name for name in folder_paths.get_filename_list(category) if fnmatch.fnmatchcase(name, pattern)]

    @classmethod
    def fingerprint_inputs(cls, category, pattern, descriptions_json):
        return tuple(cls.filenames(category, pattern))

    @classmethod
    def execute(cls, category, pattern="*", descriptions_json="{}"):
        descriptions = s.object_json(descriptions_json, "Descriptions")
        return io.NodeOutput(s.dumps({name: descriptions.get(name) for name in cls.filenames(category, pattern)}))


class JevRank(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="JevRank", display_name="Jev Rank", category="Jev/analysis",
                         description="Rank comparable Score or Boolean/Noul fields. Score values are normalized; ties preserve the supplied order.",
                         inputs=[JudgmentsType.Input("judgments"), io.String.Input("field_ids", multiline=True, default="candidate_a\ncandidate_b")],
                         outputs=[io.String.Output(display_name="best_id"), io.Float.Output(display_name="best_value"), io.String.Output(display_name="ranking_json")])

    @classmethod
    def execute(cls, judgments, field_ids):
        rows = s.rank(judgments, [key.strip() for key in field_ids.splitlines() if key.strip()])
        return io.NodeOutput(rows[0]["id"], rows[0]["value"], s.dumps(rows))


class JevWeightedScore(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="JevWeightedScore", display_name="Jev Weighted Score", category="Jev/analysis",
                         inputs=[JudgmentsType.Input("judgments"), io.String.Input("weights_json", multiline=True, default='{"adherence": 0.7, "originality": 0.3}')],
                         outputs=[io.Float.Output(display_name="score"), io.String.Output(display_name="details_json")])

    @classmethod
    def execute(cls, judgments, weights_json):
        value, rows = s.weighted_score(judgments, s.object_json(weights_json, "Weights"))
        return io.NodeOutput(value, s.dumps(rows))


NODE_CLASSES = [JevField, JevInterpret, JevResolve, JevReadString, JevReadInt, JevReadFloat, JevReadBoolean,
                JevInspectField, JevModelCandidates, JevRank, JevWeightedScore]


class JevExtension(ComfyExtension):
    async def get_node_list(self):
        return NODE_CLASSES
