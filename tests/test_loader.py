"""Load the separate checkout through the installed ComfyUI custom-node loader."""

import sys
import unittest
from unittest.mock import patch

import aiohttp

from test_jev import ROOT
from comfy.cli_args import args

args.cpu = True
import nodes


class LoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_custom_node_loader_without_network_or_installation(self):
        names = ("NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "LOADED_MODULE_DIRS", "EXTENSION_WEB_DIRS")
        previous = {name: dict(getattr(nodes, name)) for name in names}
        modules = set(sys.modules)
        try:
            with patch.object(aiohttp, "ClientSession", side_effect=aiohttp.ClientConnectionError("offline test")):
                self.assertTrue(await nodes.load_custom_node(str(ROOT)))
            for node_id in ("JevInterpret", "JevSkillChoice", "OpenRouterText"):
                node = nodes.NODE_CLASS_MAPPINGS[node_id]
                self.assertEqual(node.GET_SCHEMA().node_id, node_id)
                self.assertTrue(node.RELATIVE_PYTHON_MODULE.startswith("custom_nodes."))
            for node_id in ("JevInterpret", "JevSkillChoice"):
                inputs = nodes.NODE_CLASS_MAPPINGS[node_id].INPUT_TYPES()
                self.assertEqual(inputs["optional"]["images"][0], "IMAGE")
                self.assertFalse(inputs["optional"]["experimental_images"][1]["default"])
        finally:
            for name, mapping in previous.items():
                getattr(nodes, name).clear()
                getattr(nodes, name).update(mapping)
            for name in set(sys.modules) - modules:
                if name.startswith(str(ROOT).replace(".", "_x_")):
                    del sys.modules[name]
