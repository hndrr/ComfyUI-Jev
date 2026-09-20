"""Run real ComfyUI validation/execution with a mock TypeSafe transport."""

import copy
import json
import unittest
import tempfile
from pathlib import Path
from contextlib import ExitStack
import torch
from unittest.mock import patch

from test_jev import ROOT, api, n, response_for, completion_for
import folder_paths
from comfy.cli_args import args

args.cpu = True

import nodes
from comfy_extras.nodes_primitive import PrimitivesExtension
from comfy_extras.nodes_string import StringExtension
from comfy_extras.nodes_logic import LogicExtension
from comfy_extras.nodes_number_convert import NumberConvertExtension
from comfy_extras.nodes_math import MathExtension
from execution import PromptExecutor, validate_prompt


class Server:
    client_id = None

    def send_sync(self, *args, **kwargs):
        pass


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        catalog = patch.object(n.model_catalog, "model_ids", ("openai/gpt-4.1-mini", "openai/gpt-4.1"))
        catalog.start()
        self.addCleanup(catalog.stop)
        self.saved_nodes = dict(nodes.NODE_CLASS_MAPPINGS)
        for extension in (n.JevExtension(), PrimitivesExtension(), StringExtension(), LogicExtension(), NumberConvertExtension(), MathExtension()):
            for cls in await extension.get_node_list():
                schema = cls.GET_SCHEMA()
                nodes.NODE_CLASS_MAPPINGS[schema.node_id] = cls
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.output_dir = self.patches.enter_context(tempfile.TemporaryDirectory())
        self.patches.enter_context(patch.object(folder_paths, "get_output_directory", return_value=self.output_dir))
        self.patches.enter_context(patch.object(folder_paths, "get_filename_list", return_value=["YOUR_SD_OR_SDXL_CHECKPOINT.safetensors"]))
        class MockClip:
            def tokenize(self, text):
                return text
            def encode_from_tokens_scheduled(self, tokens):
                return {"text": tokens}
        class MockVae:
            def decode(self, latent):
                batch, _, height, width = latent.shape
                return torch.zeros((batch, height * 8, width * 8, 3))
        self.patches.enter_context(patch.object(nodes.CheckpointLoaderSimple, "load_checkpoint", return_value=(object(), MockClip(), MockVae())))
        self.sampler = self.patches.enter_context(patch.object(nodes.KSampler, "sample", side_effect=lambda **kw: (kw["latent_image"],)))

    async def asyncTearDown(self):
        nodes.NODE_CLASS_MAPPINGS.clear()
        nodes.NODE_CLASS_MAPPINGS.update(self.saved_nodes)

    async def run_prompt(self, executor, prompt, name):
        valid, error, outputs, details = await validate_prompt(name, prompt, None)
        self.assertTrue(valid, (error, details))
        self.assertFalse(details, details)
        await executor.execute_async(prompt, name, execute_outputs=outputs)
        self.assertTrue(executor.success, executor.status_messages)

    async def test_examples_reach_standard_image_nodes(self):
        async def fake(state, questions, model, **kwargs):
            return response_for(questions)

        async def generate(prompt, system, model, temperature, max_tokens, count, api_key):
            if count:
                return completion_for(json.dumps({"candidates": [
                    f"Candidate {i}: amber glass on linen\nSoft window light, close view." for i in range(count)
                ]}))
            self.assertIn("Candidate 0: amber glass", prompt)
            return completion_for("Expanded image prompt: amber glass on linen\nSoft window light, close view.")

        with patch.object(api, "evaluate", side_effect=fake), patch.object(api, "generate", side_effect=generate):
            paths = sorted((ROOT / "examples").glob("0[1-3]*.api.json"))
            self.assertEqual(len(paths), 3)
            for path in paths:
                graph = json.loads(path.read_text())
                self.assertTrue(all(not v["class_type"].startswith("Jev") or v["class_type"] == "JevInterpret" for v in graph.values()))
                executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
                await self.run_prompt(executor, copy.deepcopy(graph), path.stem)
                # Real encoders/latent/decode/save nodes; only learned model operations are mocked.
                conditioning = (await executor.caches.outputs.get("21")).outputs[0][0]
                expected = {"01": "Candidate 0: amber glass", "02": "resting on natural linen", "03": "Expanded image prompt:"}
                self.assertIn(expected[path.name[:2]], conditioning["text"])
                self.assertIn("\n", conditioning["text"])
                sampled = self.sampler.call_args.kwargs
                self.assertEqual(sampled["positive"], conditioning)
                self.assertEqual(tuple(sampled["latent_image"]["samples"].shape), (1, 4, 96, 64))
                saved = list(Path(self.output_dir).rglob(path.stem.replace(".api", "") + "_*.png"))
                self.assertEqual(len(saved), 1)

    async def test_generation_judgment_and_sampler_cache_are_independent(self):
        selected = 0

        async def fake(state, questions, model, **kwargs):
            response = response_for(questions)
            for key, question in questions.items():
                choices = list(question["criteria"])
                answer = response["answers"][key]
                answer["choice"] = choices[selected]
                answer["probabilities"] = {v: float(i == selected) for i, v in enumerate(choices)}
            return response

        async def generate(prompt, system, model, temperature, max_tokens, count, api_key):
            return completion_for(json.dumps({"candidates": [f"Prompt {i}\nComplete text" for i in range(count)]}))

        with patch.object(api, "evaluate", side_effect=fake) as judgment, patch.object(api, "generate", side_effect=generate) as generation:
            graph = json.loads((ROOT / "examples/01_generate_and_select.api.json").read_text())
            executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
            await self.run_prompt(executor, copy.deepcopy(graph), "initial")
            self.assertEqual((generation.await_count, judgment.await_count), (1, 1))
            graph["24"]["inputs"]["seed"] = 42
            graph["23"]["inputs"]["width"] = 640
            await self.run_prompt(executor, copy.deepcopy(graph), "image-settings")
            self.assertEqual((generation.await_count, judgment.await_count), (1, 1))
            self.assertEqual(self.sampler.call_args.kwargs["seed"], 42)
            self.assertEqual(self.sampler.call_args.kwargs["latent_image"]["samples"].shape[-1], 80)
            selected = 1
            graph["4"]["inputs"]["instructions"] = "Choose a more dramatic concept"
            await self.run_prompt(executor, copy.deepcopy(graph), "judgment-instructions")
            self.assertEqual((generation.await_count, judgment.await_count), (1, 2))
            self.assertEqual(self.sampler.call_args.kwargs["positive"]["text"], "Prompt 1\nComplete text")
            graph["4"]["inputs"]["refresh"] = 1
            await self.run_prompt(executor, copy.deepcopy(graph), "judgment-refresh")
            self.assertEqual((generation.await_count, judgment.await_count), (1, 3))
            graph["3"]["inputs"]["refresh"] = 1
            await self.run_prompt(executor, copy.deepcopy(graph), "generation-refresh")
            self.assertEqual(generation.await_count, 2)
            graph["1"]["inputs"]["value"] = "A new brief"
            await self.run_prompt(executor, copy.deepcopy(graph), "brief-change")
            self.assertEqual(generation.await_count, 3)

    async def test_saved_workflows_match_executable_examples(self):
        paths = list((ROOT / "examples").glob("0[1-3]*.workflow.json"))
        self.assertEqual(len(paths), 3)
        for path in paths:
            workflow = json.loads(path.read_text())
            graph = json.loads(path.with_name(path.name.replace(".workflow.json", ".api.json")).read_text())
            links = {link[0]: link for link in workflow["links"]}
            self.assertEqual({str(node["id"]) for node in workflow["nodes"]}, set(graph))
            for node in workflow["nodes"]:
                expected = graph[str(node["id"])]
                self.assertEqual(node["type"], expected["class_type"])
                self.assertNotIn("title", node)
                self.assertNotIn("_meta", expected)
                self.assertIn(node["type"], nodes.NODE_CLASS_MAPPINGS)
                slots = {slot["name"]: slot for slot in node["inputs"]}
                for name, value in expected["inputs"].items():
                    if isinstance(value, list):
                        link = links[slots[name]["link"]]
                        self.assertEqual([str(link[1]), link[2]], value)
                        self.assertEqual(link[3], node["id"])
                        self.assertEqual(node["inputs"][link[4]]["name"], name)
                    else:
                        self.assertEqual(node["widgets_values_named"][name], value)
                if node["type"] in ("JevInterpret", "OpenRouterText"):
                    self.assertEqual(node["widgets_values_named"]["api_key"], "")
                    self.assertEqual(node["widgets_values_named"]["control_after_generate"], "fixed")



if __name__ == "__main__":
    unittest.main()
