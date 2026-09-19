"""Run real ComfyUI validation/execution with a mock TypeSafe transport."""

import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from test_jev import ROOT, api, n, response_for
from comfy.cli_args import args

args.cpu = True

import nodes
from comfy_extras.nodes_preview_any import PreviewAny
from comfy_extras.nodes_primitive import PrimitivesExtension
from comfy_extras.nodes_string import StringExtension
from comfy_extras.nodes_logic import LogicExtension
from execution import PromptExecutor, validate_prompt


class Server:
    client_id = None

    def send_sync(self, *args, **kwargs):
        pass


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved_nodes = dict(nodes.NODE_CLASS_MAPPINGS)
        for extension in (n.JevExtension(), PrimitivesExtension(), StringExtension(), LogicExtension()):
            for cls in await extension.get_node_list():
                schema = cls.GET_SCHEMA()
                nodes.NODE_CLASS_MAPPINGS[schema.node_id] = cls
        nodes.NODE_CLASS_MAPPINGS["PreviewAny"] = PreviewAny

    async def asyncTearDown(self):
        nodes.NODE_CLASS_MAPPINGS.clear()
        nodes.NODE_CLASS_MAPPINGS.update(self.saved_nodes)

    async def run_prompt(self, executor, prompt, name):
        valid, error, outputs, details = await validate_prompt(name, prompt, None)
        self.assertTrue(valid, (error, details))
        self.assertFalse(details, details)
        await executor.execute_async(prompt, name, execute_outputs=outputs)
        self.assertTrue(executor.success, executor.status_messages)

    async def test_examples_and_real_cache(self):
        async def fake(state, questions, model, provider="typesafe", api_key=""):
            return response_for(questions)
        with patch.object(api, "evaluate", side_effect=fake) as transport:
            for name in ("01_brief_to_parameters", "02_asset_matching", "03_compare_concepts", "04_staged_interpretation"):
                graph = json.loads((ROOT / "examples" / (name + ".api.json")).read_text())
                executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
                await self.run_prompt(executor, copy.deepcopy(graph), name)
            graph = json.loads((ROOT / "examples/01_brief_to_parameters.api.json").read_text())
            executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
            transport.reset_mock()
            await self.run_prompt(executor, copy.deepcopy(graph), "cache-1")
            self.assertEqual(transport.await_count, 1)
            graph["6"]["inputs"]["bindings_json"] = graph["6"]["inputs"]["bindings_json"].replace('0.6', '0.9')
            await self.run_prompt(executor, copy.deepcopy(graph), "bindings-only")
            self.assertEqual(transport.await_count, 1)
            graph["5"]["inputs"]["refresh"] = 1
            await self.run_prompt(executor, copy.deepcopy(graph), "refresh")
            self.assertEqual(transport.await_count, 2)
            graph["5"]["inputs"]["provider"] = "openrouter"
            await self.run_prompt(executor, copy.deepcopy(graph), "provider-change")
            self.assertEqual(transport.await_count, 3)
            self.assertEqual(transport.call_args.kwargs["provider"], "openrouter")

    async def test_combo_connection_and_lazy_missing_value(self):
        class ComboSink:
            @classmethod
            def INPUT_TYPES(cls):
                return {"required": {"selected": (["one", "two"],)}}

            RETURN_TYPES = ()
            OUTPUT_NODE = True
            FUNCTION = "execute"

            def execute(self, selected):
                if selected != "one":
                    raise ValueError("Unexpected combo value")
                return ()

        nodes.NODE_CLASS_MAPPINGS["JevTestComboSink"] = ComboSink
        graph = {
            "1": {"class_type": "JevInterpret", "inputs": {"state": "request", "state_format": "text", "model": "jev-latest", "refresh": 0,
                "schema_json": '{"pick":{"type":"choice","instructions":"?","criteria":{"one":null,"two":null}},"missing":{"type":"extract","instructions":"Duration?"}}'}},
            "2": {"class_type": "JevResolve", "inputs": {"judgments": ["1", 0], "bindings_json": "{}"}},
            "3": {"class_type": "JevReadString", "inputs": {"result": ["2", 0], "field_id": "pick", "pointer": ""}},
            "4": {"class_type": "JevTestComboSink", "inputs": {"selected": ["3", 1]}},
            "5": {"class_type": "JevInspectField", "inputs": {"result": ["2", 0], "field_id": "missing"}},
            "6": {"class_type": "JevReadInt", "inputs": {"result": ["2", 0], "field_id": "missing", "pointer": ""}},
            "7": {"class_type": "PrimitiveInt", "inputs": {"value": 8}},
            "8": {"class_type": "ComfySwitchNode", "inputs": {"switch": ["5", 0], "on_true": ["6", 0], "on_false": ["7", 0]}},
            "9": {"class_type": "PreviewAny", "inputs": {"source": ["8", 0]}},
        }
        async def fake(state, questions, model, provider="typesafe", api_key=""):
            return response_for(questions)
        with patch.object(api, "evaluate", side_effect=fake):
            executor = PromptExecutor(Server(), cache_type=False, cache_args={"ram": 0, "ram_inactive": 0})
            await self.run_prompt(executor, graph, "native-combo-and-lazy")
            self.assertIsNone(await executor.caches.outputs.get("6"))


if __name__ == "__main__":
    unittest.main()
