import base64
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
import torch

from test_jev import ROOT, api, n, response_for, s
from test_execution import Server
import test_typesafe as direct_tests
from jev_under_test import boxes, decisions, limits, model_catalog, reference
import folder_paths
import nodes
from execution import PromptExecutor, validate_prompt
from comfy_extras.nodes_preview_any import PreviewAny


def batches():
    target = torch.zeros((2, 280, 280, 3))
    target[0, :, :, 0] = 1
    target[1, :, :, 1] = 1
    refs = torch.zeros((2, 280, 280, 3))
    refs[0, :, :, 2] = 1
    refs[1] = 1
    return target, refs


def response(task="match", value=0.95, confidence=0.9):
    answer = {"type": "noul", "noul": value} if task == "match" else {
        "type": "score", "score": value * 2, "confidence": confidence,
        "probabilities": {"0": 0.05, "1": 0.0, "2": 0.95}, "legend": {"0": "no", "1": "partly", "2": "yes"}}
    return {"model": "jev-1.14.0", "answers": {"evaluation": answer}, "usage": {"input_tokens": 100}}


def pixel(part, assets=None):
    data = assets[part]["data_base64"] if assets else part["image_url"]["url"].split(",")[1]
    with Image.open(BytesIO(base64.b64decode(data))) as image:
        return image.getpixel((0, 0))


class ReferenceTests(unittest.TestCase):
    def test_prepare_keeps_target_and_selected_reference_in_distinct_roles(self):
        images, refs = batches()
        for provider, model in (("typesafe", "jev-preview"), ("openrouter", "typesafe/jev-1.14")):
            for task in ("match", "score"):
                for placement in ("instructions", "criteria"):
                    state, questions, metadata = reference.prepare(images, refs, "Compare the product", task, 1, 0, placement, model, provider)
                    q = questions["evaluation"]
                    part = q["instructions"]["reference_image"] if placement == "instructions" else (q["criteria"]["true"] if task == "match" else q["criteria"][-1])["reference_image"]
                    self.assertEqual(pixel(state["evaluation_image"]), (0, 255, 0))
                    self.assertEqual(pixel(part), (0, 0, 255))
                    self.assertEqual(metadata["evaluation"]["image_index"], 1)
                    self.assertEqual(metadata["reference"]["image_index"], 0)
                    self.assertEqual(metadata["evaluation"]["source_hash"], boxes.image_fingerprint(images[1:2]))
                    self.assertEqual(set(state), {"evaluation_image"})

    def test_all_offered_models_are_verified_jev_routes(self):
        images, refs = batches()
        for provider, choices in reference.model_choices().items():
            for model in choices:
                state, questions, _ = reference.prepare(images, refs, "Compare", "match", 0, 1, "instructions", model, provider)
                body = decisions.payload(state, questions, model, provider)
                self.assertEqual(len(body.get("assets", {})), 2 if provider == "typesafe" else 0)

    def test_score_gate_uses_value_and_confidence_independently(self):
        images, refs = batches()
        _, questions, metadata = reference.prepare(images, refs, "Compare", "score", 0, 0, "criteria", "jev-preview", "typesafe")
        for value, confidence, reason in ((0.79, 0.99, "below_threshold"), (0.99, 0.49, "low_confidence"), (0.8, 0.5, None)):
            result = reference.collect(response("score", value, confidence), questions["evaluation"], "score", 0.8, 0.5, metadata, "criteria", "typesafe")
            self.assertEqual(result[0], value)
            self.assertEqual(result[1], reason is None)
            self.assertEqual(result[2]["reason"], reason)

    def test_match_has_no_invented_confidence(self):
        images, refs = batches()
        _, questions, metadata = reference.prepare(images, refs, "Compare", "match", 0, 0, "instructions", "jev-preview", "typesafe")
        for probability, accepted in ((0.05, False), (0.8, True)):
            _, actual, details = reference.collect(response(value=probability), questions["evaluation"], "match", 0.8, 1.0, metadata, "instructions", "typesafe")
            self.assertEqual(actual, accepted)
            self.assertIsNone(details["confidence"])

    def test_invalid_indices_do_not_pair_or_broadcast_batches(self):
        images, refs = batches()
        for target_index, reference_index in ((-1, 0), (2, 0), (0, 2), (0, -1), (True, 0), (0, 0.5)):
            with self.subTest(indices=(target_index, reference_index)), self.assertRaises(ValueError):
                reference.prepare(images, refs, "Compare", "match", target_index, reference_index, "instructions", "jev-preview", "typesafe")

    def test_nonselected_frames_do_not_enter_the_request(self):
        images, refs = batches()
        before = reference.prepare(images, refs, "Compare", "match", 1, 0, "instructions", "jev-preview", "typesafe")
        images[0] = 0.35
        refs[1] = 0.25
        after = reference.prepare(images, refs, "Compare", "match", 1, 0, "instructions", "jev-preview", "typesafe")
        self.assertEqual(before, after)

    def test_resize_is_only_for_upload_and_preserves_source_identity(self):
        images, refs = torch.zeros((1, 80, 100, 3)), torch.ones((1, 2000, 1500, 3))
        originals = (images.clone(), refs.clone())
        _, _, metadata = reference.prepare(images, refs, "Compare", "match", 0, 0, "instructions", "jev-preview", "typesafe")
        for role, original in zip(("evaluation", "reference"), originals):
            self.assertEqual(metadata[role]["source_hash"], boxes.image_fingerprint(original))
            self.assertGreaterEqual(min(metadata[role]["upload_size"].values()), 280)
            self.assertLessEqual(max(metadata[role]["upload_size"].values()), 1568)
        self.assertTrue(torch.equal(images, originals[0]))
        self.assertTrue(torch.equal(refs, originals[1]))

    def test_nested_images_are_explicitly_rejected_for_other_decisions_models(self):
        image = direct_tests.image_part()
        architecture = {"input_modalities": ["text", "image"], "output_modalities": ["decisions"]}
        with patch.dict(model_catalog.decision_models, {"vendor/decision": architecture}):
            for model in ("openai/gpt-6-luna-decisions", "cloudflare/clef", "vendor/decision"):
                for state, questions in (({"evaluation_image": image}, {"q": {"type": "noul"}}),
                                         ("text", {"q": {"type": "noul", "instructions": {"reference": image}}})):
                    with self.subTest(model=model), self.assertRaisesRegex(ValueError, "verified Jev"):
                        decisions.payload(state, questions, model, "openrouter")

    def test_nested_reference_size_and_shape_are_validated(self):
        for part in (direct_tests.image_part(size=(64, 64)), {"type": "image_url", "image_url": {}},
                     {"type": "image_url", "image_url": {"url": "https://example.invalid/private.png"}}):
            for provider, model in (("typesafe", "jev-preview"), ("openrouter", "typesafe/jev-1.14")):
                with self.assertRaises(ValueError):
                    decisions.payload("text", {"q": {"type": "noul", "instructions": {"reference": part}}}, model, provider)

    def test_criteria_assets_obey_encoded_body_budget_and_do_not_mutate_inputs(self):
        images, refs = batches()
        state, questions, _ = reference.prepare(images, refs, "Compare", "score", 1, 0, "criteria", "jev-preview", "typesafe")
        original = copy.deepcopy((state, questions))
        body = decisions.payload(state, questions, "jev-preview", "typesafe")
        size = len(decisions.encode_payload(body))
        with patch.object(limits, "JEV_REQUEST_BYTES", size):
            decisions.payload(state, questions, "jev-preview", "typesafe")
        with patch.object(limits, "JEV_REQUEST_BYTES", size - 1), self.assertRaisesRegex(ValueError, "32 MiB"):
            decisions.payload(state, questions, "jev-preview", "typesafe")
        self.assertEqual((state, questions), original)
        self.assertEqual(len(body["assets"]), 2)

    def test_malformed_response_never_becomes_accepted(self):
        images, refs = batches()
        _, questions, metadata = reference.prepare(images, refs, "Compare", "score", 0, 0, "criteria", "jev-preview", "typesafe")
        invalid = [None, {}, {"answers": []}]
        for field, value in (("type", "noul"), ("score", 2.1), ("confidence", float("nan")), ("probabilities", {})):
            item = response("score")
            item["answers"]["evaluation"][field] = value
            invalid.append(item)
        for item in invalid:
            with self.assertRaises(ValueError):
                reference.collect(item, questions["evaluation"], "score", 0.8, 0.5, metadata, "criteria", "typesafe")


class ReferenceTransportTests(unittest.IsolatedAsyncioTestCase):
    setUp = direct_tests.TypeSafeTests.setUp

    async def test_direct_assets_preserve_roles_for_both_tasks_and_placements(self):
        images, refs = batches()
        for task in ("match", "score"):
            for placement in ("instructions", "criteria"):
                self.response.text.return_value = s.dumps(response(task))
                result = await n.JevReferenceEvaluate.execute(images, refs, "Compare", task, 1, 0,
                    model="jev-1.14.0", provider="typesafe", reference_placement=placement)
                call = self.session.post.call_args
                body = json.loads(call.kwargs["data"])
                q = body["questions"]["evaluation"]
                ref = q["instructions"]["reference_image"] if placement == "instructions" else (q["criteria"]["true"] if task == "match" else q["criteria"][-1])["reference_image"]
                self.assertEqual(pixel(body["state"]["evaluation_image"], body["assets"]), (0, 255, 0))
                self.assertEqual(pixel(ref, body["assets"]), (0, 0, 255))
                self.assertNotEqual(ref, body["state"]["evaluation_image"])
                self.assertEqual(len(body["assets"]), 2)
                self.assertEqual(call.kwargs["headers"]["TypeSafe-Danger-Zone-Images"], "true")
                self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer direct-test-key")
                self.assertTrue(result.result[1])

    async def test_openrouter_uses_nested_image_parts_without_direct_assets_or_header(self):
        self.response.text.return_value = s.dumps(response())
        result = await n.JevReferenceEvaluate.execute(*batches(), "Compare", image_index=1, reference_index=0)
        call = self.session.post.call_args
        body = json.loads(call.kwargs["data"])
        self.assertEqual(pixel(body["state"]["evaluation_image"]), (0, 255, 0))
        self.assertEqual(pixel(body["questions"]["evaluation"]["instructions"]["reference_image"]), (0, 0, 255))
        self.assertNotIn("assets", body)
        self.assertNotIn("TypeSafe-Danger-Zone-Images", call.kwargs["headers"])
        self.assertEqual(call.args[0], api.OPENROUTER_ENDPOINT)
        self.assertEqual(result.result[2]["provider"], "openrouter")

    async def test_question_only_image_also_enables_direct_header(self):
        self.response.text.return_value = s.dumps({"answers": {"q": {"type": "noul", "noul": 0.9}}})
        await api.evaluate("text", {"q": {"type": "noul", "instructions": {"reference": direct_tests.image_part()}}}, "jev-preview", "typesafe")
        call = self.session.post.call_args
        self.assertEqual(len(json.loads(call.kwargs["data"])["assets"]), 1)
        self.assertEqual(call.kwargs["headers"]["TypeSafe-Danger-Zone-Images"], "true")

    async def test_unsupported_models_and_invalid_inputs_never_call_transport(self):
        for kwargs in ({"model": "cloudflare/clef"}, {"model": "openai/gpt-6-luna-decisions"},
                       {"provider": "typesafe", "model": "typesafe/jev-1.14"}, {"threshold": 2},
                       {"min_confidence": True}, {"instructions": ""}, {"reference_index": 2},
                       {"reference_placement": "state"}, {"task": "unknown"}):
            params = {"images": batches()[0], "reference_images": batches()[1], "instructions": "Compare", **kwargs}
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await n.JevReferenceEvaluate.execute(**params)
        self.transport.assert_not_called()


class ReferenceWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_provider_examples_execute_and_cache_selected_pair(self):
        saved = dict(nodes.NODE_CLASS_MAPPINGS)
        self.addCleanup(lambda: (nodes.NODE_CLASS_MAPPINGS.clear(), nodes.NODE_CLASS_MAPPINGS.update(saved)))
        for cls in (n.JevReferenceEvaluate, PreviewAny, nodes.LoadImage):
            nodes.NODE_CLASS_MAPPINGS[cls.GET_SCHEMA().node_id if hasattr(cls, "GET_SCHEMA") else cls.__name__] = cls
        for stem in ("07_reference_evaluate", "08_reference_evaluate_typesafe"):
            graph = json.loads((ROOT / f"examples/{stem}.api.json").read_text())
            with tempfile.TemporaryDirectory() as directory, patch.object(folder_paths, "get_input_directory", return_value=directory):
                Image.new("RGB", (400, 300), "red").save(Path(directory) / "EVALUATION_IMAGE.png")
                Image.new("RGB", (400, 300), "blue").save(Path(directory) / "REFERENCE_IMAGE.png")
                async def post(endpoint, body, *args): return response_for(body["questions"])
                with patch.object(api, "_post_json", side_effect=post) as transport:
                    valid, error, outputs, details = await validate_prompt(stem, graph, None)
                    self.assertTrue(valid, (error, details))
                    executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
                    for run, expected_calls in (("first", 1), ("cached", 1), ("refresh", 2)):
                        if run == "refresh": graph["3"]["inputs"]["refresh"] += 1
                        await executor.execute_async(copy.deepcopy(graph), stem + run, execute_outputs=outputs)
                        self.assertTrue(executor.success, executor.status_messages)
                        self.assertEqual(transport.await_count, expected_calls)
                    node_details = (await executor.caches.outputs.get("3")).outputs[2][0]
                    self.assertNotIn("legend", node_details["answer"])
                    self.assertLess(len(json.dumps(node_details)), 5000)
                    body = transport.call_args.args[1]
                    self.assertIn("evaluation_image", body["state"])
                    self.assertEqual(len(body.get("assets", {})), 2 if graph["3"]["inputs"]["provider"] == "typesafe" else 0)

    def test_workflows_match_api_graph_and_new_node_schema(self):
        for stem in ("07_reference_evaluate", "08_reference_evaluate_typesafe"):
            workflow = json.loads((ROOT / f"examples/{stem}.workflow.json").read_text())
            graph = json.loads((ROOT / f"examples/{stem}.api.json").read_text())
            links = {link[0]: link for link in workflow["links"]}
            for node in workflow["nodes"]:
                expected = graph[str(node["id"])]
                self.assertEqual(node["type"], expected["class_type"])
                for name, value in expected["inputs"].items():
                    if isinstance(value, list):
                        slot = next(slot for slot in node["inputs"] if slot["name"] == name)
                        link = links[slot["link"]]
                        self.assertEqual([str(link[1]), link[2]], value)
                        self.assertEqual(link[3], node["id"])
                    else: self.assertEqual(node["widgets_values_named"][name], value)
                if node["type"] == "JevReferenceEvaluate":
                    schema = n.JevReferenceEvaluate.INPUT_TYPES()
                    names = list(schema["required"]) + list(schema["optional"])
                    self.assertEqual([slot["name"] for slot in node["inputs"]], names)
                    widgets = [name for name in names if name not in ("images", "reference_images")]
                    widgets.insert(widgets.index("refresh") + 1, "control_after_generate")
                    self.assertEqual(node["widgets_values"], [node["widgets_values_named"][name] for name in widgets])
                    self.assertEqual(node["widgets_values_named"]["api_key"], "")
