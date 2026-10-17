import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import torch
from PIL import Image

from test_jev import ROOT, api, n, s
from jev_under_test import boxes, decisions, media, model_catalog
from test_execution import Server
import folder_paths
import nodes
from execution import PromptExecutor, validate_prompt
from comfy_extras.nodes_mask import MaskToImage
from comfy_extras.nodes_preview_any import PreviewAny


BOX = {"left": 0.251, "top": 0.201, "right": 0.749, "bottom": 0.799}


def response(box=None, presence=0.95, confidence=0.9):
    return {"model": "typesafe/jev-1.14", "answers": {
        "target": {"type": "box", "box": dict(BOX if box is None else box), "confidence": confidence},
        "present": {"type": "noul", "noul": presence},
    }, "usage": {"input_tokens": 100, "output_tokens": 21}}


def box_data(images, image_index=0, **kwargs):
    return {**s.collect_box(response(**kwargs)), "image_index": image_index, "batch_size": images.shape[0],
            "width": images.shape[2], "height": images.shape[1],
            "source_hash": boxes.image_fingerprint(images[image_index:image_index + 1])}


class BoxTests(unittest.TestCase):
    def test_presence_and_confidence_are_independent_gates(self):
        self.assertEqual(s.collect_box(response())["box"], BOX)
        for probability, confidence, reason in ((0.2, 0.99, "not_found"), (0.99, 0.2, "low_confidence")):
            result = s.collect_box(response(presence=probability, confidence=confidence))
            self.assertFalse(result["found"])
            self.assertIsNone(result["box"])
            self.assertEqual(result["raw_box"], BOX)
            self.assertEqual(result["reason"], reason)
        self.assertTrue(s.collect_box(response(presence=0.8, confidence=0.5))["found"])
        self.assertFalse(s.collect_box(response(), presence_threshold=0.99)["found"])
        self.assertFalse(s.collect_box(response(), min_confidence=0.99)["found"])

    def test_malformed_answers_never_become_usable_coordinates(self):
        invalid = [None, {}, {"answers": []}]
        for question, field, value in (
            ("target", "box", None), ("target", "box", []), ("target", "box", {"left": 0}),
            ("target", "type", "score"), ("present", "type", "box"), ("present", "noul", True),
            ("present", "noul", float("nan")), ("present", "noul", "0.9"),
            ("target", "confidence", -0.1), ("target", "confidence", float("inf")),
        ):
            item = response()
            item["answers"][question][field] = value
            invalid.append(item)
        for field, value in (("left", -0.01), ("right", 1.01), ("top", True), ("bottom", "0.8"),
                             ("left", float("nan")), ("right", BOX["left"]), ("bottom", 0.1)):
            item = response()
            item["answers"]["target"]["box"][field] = value
            invalid.append(item)
        for item in invalid:
            with self.subTest(response=item), self.assertRaises(ValueError):
                s.collect_box(item)
        for item in ("", "  ", None):
            with self.assertRaises(ValueError):
                s.box_questions(item)

    def test_box_requires_verified_model_and_image_before_transport(self):
        state = media.prepare("photo", images=torch.zeros((1, 280, 280, 3)))
        questions = s.box_questions("the red mug")
        self.assertEqual(set(questions["target"]), {"type", "instructions"})
        for model, provider in (("typesafe/jev-1.13", "openrouter"), ("cloudflare/clef", "openrouter"),
                                ("jev-latest", "typesafe")):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, "Box requires Jev 1.14"):
                decisions.payload(state, questions, model, provider)
        with self.assertRaisesRegex(ValueError, "requires an image"):
            decisions.payload("no image", questions, decisions.BOX_MODELS[0], "openrouter")
        for model in decisions.BOX_MODELS:
            body = decisions.payload(state, questions, model, "openrouter")
            self.assertEqual(body["state"], list(state.parts))
            self.assertEqual(body["questions"], questions)
            self.assertNotIn("provider", body)
            self.assertIn("image", model_catalog.BUILTIN_DECISIONS[model]["input_modalities"])

    def test_upload_resize_preserves_original_coordinate_system(self):
        for width, height in ((80, 80), (256, 256), (1024, 768), (1920, 1080), (3000, 500)):
            with self.subTest(size=(width, height)):
                frame = torch.zeros((1, height, width, 3))
                context, uploaded = boxes.context(frame)
                self.assertGreaterEqual(min(uploaded.values()), 256)
                self.assertLessEqual(max(uploaded.values()), 1568)
                self.assertGreaterEqual(uploaded["width"] * uploaded["height"], 78400)
                self.assertEqual(len(context.parts), 2)
                self.assertEqual(frame.shape[1:3], (height, width))
                crop, mask, _, details = boxes.region(frame, box_data(frame))
                pixels = details["pixels"]
                self.assertEqual(crop.shape[1:3], (pixels["height"], pixels["width"]))
                self.assertEqual(mask.shape[1:3], (height, width))
        with self.assertRaisesRegex(ValueError, "aspect ratio"):
            boxes.context(torch.zeros((1, 10, 4000, 3)))

    def test_crop_mask_preview_and_coordinates_use_same_padded_rectangle(self):
        images = torch.zeros((2, 100, 200, 3))
        images[1] = 0.4
        original = images.clone()
        crop, mask, preview, details = boxes.region(images, box_data(images, 1), padding=5)
        self.assertEqual(details["pixels"], {"left": 45, "top": 15, "right": 155, "bottom": 85, "width": 110, "height": 70})
        self.assertEqual(details["effective_box"], {"left": 0.225, "top": 0.15, "right": 0.775, "bottom": 0.85})
        self.assertEqual(tuple(crop.shape), (1, 70, 110, 3))
        self.assertTrue(torch.equal(crop, images[1:2, 15:85, 45:155]))
        self.assertEqual(mask.sum().item(), 110 * 70)
        self.assertTrue(torch.all(mask[:, 15:85, 45:155] == 1))
        self.assertTrue(torch.all(mask[:, :15] == 0))
        self.assertEqual(preview[0, 15, 45, 0].item(), 1)
        self.assertTrue(torch.equal(preview[:, 30:60, 60:100], images[1:2, 30:60, 60:100]))
        self.assertTrue(torch.equal(images, original))
        crop.fill_(0)
        self.assertTrue(torch.equal(images, original))

    def test_padding_clamps_edges_and_small_boxes_cover_at_least_one_pixel(self):
        images = torch.zeros((1, 10, 20, 3))
        crop, mask, _, details = boxes.region(images, box_data(images), padding=1000)
        self.assertEqual(details["pixels"], {"left": 0, "top": 0, "right": 20, "bottom": 10, "width": 20, "height": 10})
        self.assertEqual(crop.shape, images.shape)
        self.assertTrue(torch.all(mask == 1))
        tiny = {"left": 0.999, "top": 0.999, "right": 1, "bottom": 1}
        crop, mask, _, _ = boxes.region(images, box_data(images, box=tiny))
        self.assertEqual(tuple(crop.shape), (1, 1, 1, 3))
        self.assertEqual(mask.sum().item(), 1)

    def test_missing_target_stops_or_returns_explicit_empty_mask(self):
        images = torch.rand((2, 50, 80, 3))
        data = box_data(images, 1, presence=0.01)
        with self.assertRaisesRegex(ValueError, "did not accept"):
            boxes.region(images, data)
        crop, mask, preview, details = boxes.region(images, data, padding=10, on_missing="keep_image")
        self.assertTrue(torch.equal(crop, images[1:2]))
        self.assertTrue(torch.equal(preview, images[1:2]))
        self.assertFalse(details["found"])
        self.assertIsNone(details["pixels"])
        self.assertEqual(mask.sum().item(), 0)

    def test_batch_and_dictionary_boundary_validation(self):
        images = torch.zeros((2, 50, 80, 3))
        data = box_data(images)
        for field, value in (("image_index", -1), ("image_index", 2), ("image_index", True),
                             ("width", 81), ("batch_size", 1), ("found", "true"), ("box", {})):
            with self.subTest(field=field), self.assertRaises(ValueError):
                boxes.region(images, {**data, field: value})
        for padding in (-1, 0.5, True):
            with self.assertRaises(ValueError):
                boxes.region(images, data, padding=padding)
        for malformed in (torch.zeros((0, 5, 5, 3)), torch.zeros((1, 5, 5)), torch.zeros((1, 5, 5, 2)),
                          torch.full((1, 5, 5, 3), float("nan")), torch.ones((1, 5, 5, 3), dtype=torch.int64)):
            with self.assertRaises(ValueError):
                boxes.selected_image(malformed, 0)

    def test_same_size_image_changes_and_batch_reordering_are_rejected(self):
        images = torch.zeros((2, 30, 40, 3))
        images[1] = 0.7
        data = box_data(images, 1)
        changed = images.clone()
        changed[1, 0, 0, 0] += 0.1
        for candidate in (changed, images.flip(0), images.double()):
            with self.subTest(dtype=candidate.dtype), self.assertRaisesRegex(ValueError, "Selected image changed"):
                boxes.region(candidate, data)
        with self.assertRaisesRegex(ValueError, "Selected image changed"):
            boxes.region(images, {**data, "image_index": 0})
        with self.assertRaisesRegex(ValueError, "Selected image changed"):
            boxes.region(images, {key: value for key, value in data.items() if key != "source_hash"})
        # A distinct tensor with identical selected pixels is safe; unselected frames do not matter.
        changed = images.clone()
        changed[0] = 0.9
        self.assertTrue(torch.equal(boxes.region(changed, data)[0], boxes.region(images, data)[0]))

    def test_missing_target_never_creates_a_mask_even_with_high_box_confidence(self):
        images = torch.zeros((1, 30, 40, 3))
        data = box_data(images, presence=0, confidence=1)
        with self.assertRaisesRegex(ValueError, "did not accept"):
            boxes.region(images, data)
        self.assertEqual(boxes.region(images, data, padding=1000, on_missing="keep_image")[1].sum().item(), 0)

    def test_grayscale_and_rgba_outputs_keep_dtype_channels_and_alpha(self):
        for channels in (1, 4):
            images = torch.full((1, 30, 40, channels), 0.4, dtype=torch.float64)
            crop, mask, preview, _ = boxes.region(images, box_data(images))
            self.assertEqual((crop.shape[-1], preview.shape[-1]), (channels, channels))
            self.assertEqual((crop.dtype, mask.dtype, preview.dtype), (images.dtype,) * 3)
            if channels == 4:
                self.assertTrue(torch.equal(preview[..., 3], images[..., 3]))


class BoxNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_frame_only_uses_one_paired_request(self):
        images = torch.zeros((3, 280, 280, 3))
        images[1] = 1
        with patch.object(api, "evaluate", new_callable=AsyncMock, return_value=response()) as evaluate:
            output = await n.JevFindBox.execute(images, "the white mug", image_index=1)
        data, found, confidence = output.result
        self.assertEqual((data["image_index"], data["batch_size"], found, confidence), (1, 3, True, 0.9))
        self.assertEqual(data["source_hash"], boxes.image_fingerprint(images[1:2]))
        evaluate.assert_awaited_once()
        context, questions, model = evaluate.call_args.args
        self.assertEqual(context, media.prepare("Locate the described target in this image.", images=images[1:2]))
        self.assertEqual([q["type"] for q in questions.values()], ["box", "noul"])
        self.assertEqual(evaluate.call_args.kwargs["provider"], "openrouter")
        self.assertEqual(model, "typesafe/jev-1.14")

    async def test_transport_serializes_documented_wire_format(self):
        reply = MagicMock(status=200)
        reply.text = AsyncMock(return_value=s.dumps(response()))
        reply.__aenter__ = AsyncMock(return_value=reply)
        reply.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = reply
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api.aiohttp, "ClientSession", return_value=session):
            output = await n.JevFindBox.execute(torch.zeros((1, 64, 64, 3)), "the mug", api_key="synthetic-test-key")
        body = json.loads(session.post.call_args.kwargs["data"])
        self.assertEqual(set(body), {"model", "state", "questions"})
        self.assertEqual(body["model"], "typesafe/jev-1.14")
        self.assertEqual(body["state"][1]["type"], "image_url")
        self.assertNotIn("criteria", body["questions"]["target"])
        self.assertTrue(output.result[1])
        self.assertEqual(session.post.call_args.args[0], api.OPENROUTER_ENDPOINT)

    async def test_invalid_model_target_index_or_threshold_never_calls_api(self):
        with patch.object(api, "evaluate", new_callable=AsyncMock) as evaluate:
            for kwargs in ({"model": "typesafe/jev-1.13"}, {"image_index": 1}, {"presence_threshold": 2},
                           {"min_confidence": True}, {"target": ""}):
                arguments = {"images": torch.zeros((1, 280, 280, 3)), "target": "mug", **kwargs}
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    await n.JevFindBox.execute(**arguments)
        evaluate.assert_not_awaited()

    async def test_real_comfy_execution_and_local_padding_cache(self):
        saved_nodes = dict(nodes.NODE_CLASS_MAPPINGS)
        self.addCleanup(lambda: (nodes.NODE_CLASS_MAPPINGS.clear(), nodes.NODE_CLASS_MAPPINGS.update(saved_nodes)))
        for cls in (n.JevFindBox, n.JevBoxRegion, nodes.EmptyImage, nodes.SaveImage):
            nodes.NODE_CLASS_MAPPINGS[cls.GET_SCHEMA().node_id if hasattr(cls, "GET_SCHEMA") else cls.__name__] = cls
        graph = {
            "1": {"class_type": "EmptyImage", "inputs": {"width": 280, "height": 280, "batch_size": 2, "color": 0x444444}},
            "2": {"class_type": "JevFindBox", "inputs": {"images": ["1", 0], "target": "the mug", "image_index": 1,
                "model": "typesafe/jev-1.14", "api_key": "", "refresh": 0}},
            "3": {"class_type": "JevBoxRegion", "inputs": {"images": ["1", 0], "box_data": ["2", 0], "padding": 0, "on_missing": "error"}},
            "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "crop"}},
            "5": {"class_type": "SaveImage", "inputs": {"images": ["3", 2], "filename_prefix": "preview"}},
        }
        with tempfile.TemporaryDirectory() as output_dir, patch.object(folder_paths, "get_output_directory", return_value=output_dir), patch.object(api, "evaluate", new_callable=AsyncMock, return_value=response()) as evaluate:
            executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
            for name, padding, refresh, expected_calls in (("first", 0, 0, 1), ("padding", 10, 0, 1), ("refresh", 10, 1, 2)):
                graph["3"]["inputs"]["padding"] = padding
                graph["2"]["inputs"]["refresh"] = refresh
                valid, error, outputs, details = await validate_prompt(name, graph, None)
                self.assertTrue(valid, (error, details))
                await executor.execute_async(copy.deepcopy(graph), name, execute_outputs=outputs)
                self.assertTrue(executor.success, executor.status_messages)
                self.assertEqual(evaluate.await_count, expected_calls)
                region = (await executor.caches.outputs.get("3")).outputs
                self.assertEqual(region[3][0]["image_index"], 1)
                self.assertEqual(region[0][0].shape[0], 1)
                self.assertEqual(region[1][0].shape, (1, 280, 280))
            self.assertEqual(len(list(Path(output_dir).glob("*.png"))), 6)

    async def test_bundled_example_reaches_all_previews(self):
        saved_nodes = dict(nodes.NODE_CLASS_MAPPINGS)
        self.addCleanup(lambda: (nodes.NODE_CLASS_MAPPINGS.clear(), nodes.NODE_CLASS_MAPPINGS.update(saved_nodes)))
        for cls in (n.JevFindBox, n.JevBoxRegion, MaskToImage, PreviewAny, nodes.LoadImage, nodes.PreviewImage):
            nodes.NODE_CLASS_MAPPINGS[cls.GET_SCHEMA().node_id if hasattr(cls, "GET_SCHEMA") else cls.__name__] = cls
        for stem in ("05_find_box", "06_find_box_typesafe"):
            graph = json.loads((ROOT / f"examples/{stem}.api.json").read_text())
            with tempfile.TemporaryDirectory() as directory, patch.object(folder_paths, "get_input_directory", return_value=directory), patch.object(folder_paths, "get_temp_directory", return_value=directory), patch.object(api, "evaluate", new_callable=AsyncMock, return_value=response()) as evaluate:
                Image.new("RGB", (400, 300), "gray").save(Path(directory) / "YOUR_IMAGE.png")
                valid, error, outputs, details = await validate_prompt("box-example", graph, None)
                self.assertTrue(valid, (error, details))
                executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
                await executor.execute_async(graph, "box-example", execute_outputs=outputs)
                self.assertTrue(executor.success, executor.status_messages)
                evaluate.assert_awaited_once()
                self.assertEqual(evaluate.call_args.kwargs["provider"], graph["2"]["inputs"]["provider"])
                self.assertEqual(len(list(Path(directory).glob("ComfyUI_temp_*.png"))), 3)
                result = (await executor.caches.outputs.get("8")).outputs[0][0]
                self.assertEqual(json.loads(result)["image_index"], 0)
                self.assertIn('"effective_box"', result)

    def test_workflow_matches_api_graph_and_box_schemas(self):
        for stem in ("05_find_box", "06_find_box_typesafe"):
            workflow = json.loads((ROOT / f"examples/{stem}.workflow.json").read_text())
            graph = json.loads((ROOT / f"examples/{stem}.api.json").read_text())
            links = {link[0]: link for link in workflow["links"]}
            by_id = {node["id"]: node for node in workflow["nodes"]}
            self.assertEqual({str(node_id) for node_id in by_id}, set(graph))
            for node in workflow["nodes"]:
                expected = graph[str(node["id"])]
                self.assertEqual(node["type"], expected["class_type"])
                for name, value in expected["inputs"].items():
                    if isinstance(value, list):
                        slot = next(slot for slot in node["inputs"] if slot["name"] == name)
                        link = links[slot["link"]]
                        self.assertEqual([str(link[1]), link[2]], value)
                        self.assertEqual(link[3], node["id"])
                        self.assertIn(link[0], by_id[link[1]]["outputs"][link[2]]["links"])
                        self.assertEqual(node["inputs"][link[4]]["name"], name)
                    else:
                        self.assertEqual(node["widgets_values_named"][name], value)
            for node_id, cls in ((2, n.JevFindBox), (3, n.JevBoxRegion)):
                node = by_id[node_id]
                schema = cls.INPUT_TYPES()
                names = list(schema["required"]) + list(schema.get("optional", {}))
                self.assertEqual([slot["name"] for slot in node["inputs"]], names)
                widget_names = [name for name in names if name not in ("images", "box_data")]
                if "refresh" in widget_names:
                    widget_names.insert(widget_names.index("refresh") + 1, "control_after_generate")
                self.assertEqual(node["widgets_values"], [node["widgets_values_named"][name] for name in widget_names])
                self.assertEqual([output["type"] for output in node["outputs"]], cls.RETURN_TYPES)
            self.assertEqual(by_id[2]["widgets_values_named"]["api_key"], "")
            self.assertEqual(by_id[2]["widgets_values_named"]["control_after_generate"], "fixed")


if __name__ == "__main__":
    unittest.main()
