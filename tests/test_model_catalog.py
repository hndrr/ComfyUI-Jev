import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from test_jev import api, n, package


catalog = package.model_catalog


def model(model_id, inputs=None, outputs=None):
    return {"id": model_id, "architecture": {
        "input_modalities": inputs if inputs is not None else ["text"],
        "output_modalities": outputs if outputs is not None else ["text"],
    }}


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache_path = Path(directory.name) / "catalog.json"
        for target, value in (("CACHE_PATH", self.cache_path), ("model_ids", ())):
            patcher = patch.object(catalog, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.response = MagicMock(status=200)
        self.response.text = AsyncMock(return_value=json.dumps({"data": [model("vendor/new-model")]}))
        self.response.__aenter__ = AsyncMock(return_value=self.response)
        self.response.__aexit__ = AsyncMock(return_value=False)
        self.session = MagicMock()
        self.session.get.return_value = self.response
        self.session.__aenter__ = AsyncMock(return_value=self.session)
        self.session.__aexit__ = AsyncMock(return_value=False)
        patcher = patch.object(api.aiohttp, "ClientSession", return_value=self.session)
        self.transport = patcher.start()
        self.addCleanup(patcher.stop)

    async def test_catalog_filters_deduplicates_and_uses_public_get(self):
        self.response.text.return_value = json.dumps({"data": [
            model("z/text"), model("a/vision", inputs=["text", "image"]), model("z/text"),
            model("b/image", outputs=["image"]), model("c/transcribe", inputs=["audio"]),
            model("d/embed", outputs=["embeddings"]), model(""), model("custom"),
            {}, None, {"id": "bad", "architecture": None},
        ]})
        self.assertEqual(await api.list_text_models(), ("a/vision", "z/text"))
        self.session.get.assert_called_once_with(api.MODELS_ENDPOINT, allow_redirects=False)
        self.assertEqual(self.transport.call_args.kwargs["timeout"].total, 10)
        self.session.post.assert_not_called()

    async def test_catalog_failures_are_explicit(self):
        for payload in ({}, {"data": {}}, {"data": []}, {"data": [model("image", outputs=["image"])]}):
            self.response.text.return_value = json.dumps(payload)
            with self.assertRaises(ValueError):
                await api.list_text_models()
        self.response.status = 503
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            await api.list_text_models()
        for error, message in ((asyncio.TimeoutError(), "timed out"), (api.aiohttp.ClientError(), "connection failed")):
            self.session.get.side_effect = error
            with self.assertRaisesRegex(RuntimeError, message):
                await api.list_text_models()

    async def test_entrypoint_populates_schema_and_saves_catalog(self):
        extension = await package.comfy_entrypoint()
        self.assertIn(n.OpenRouterText, await extension.get_node_list())
        options = n.OpenRouterText.INPUT_TYPES()["required"]["model"][1]["options"]
        self.assertEqual([option["key"] for option in options], ["vendor/new-model", "custom"])
        self.assertEqual(options[-1]["inputs"]["required"]["model_id"][1]["default"], "")
        self.assertEqual(json.loads(self.cache_path.read_text()), ["vendor/new-model"])
        for _ in range(3):
            n.OpenRouterText.INPUT_TYPES()
        self.session.get.assert_called_once()

    async def test_restart_refreshes_catalog_and_failed_fetch_uses_saved_list(self):
        await catalog.load()
        catalog.model_ids = ()
        self.response.status = 503
        with self.assertLogs(level="WARNING") as logged:
            await catalog.load()
        self.assertEqual(catalog.model_ids, ("vendor/new-model",))
        self.assertIn("using the last model list", logged.output[0])
        self.response.status = 200
        self.response.text.return_value = json.dumps({"data": [model("vendor/newer-model")]})
        await catalog.load()
        self.assertEqual(catalog.model_ids, ("vendor/newer-model",))
        self.assertEqual(json.loads(self.cache_path.read_text()), ["vendor/newer-model"])

    async def test_missing_or_invalid_cache_keeps_custom_available(self):
        self.response.status = 503
        for cached in (None, "not json", "[]", '[null]', '[""]', '["custom"]'):
            if cached is not None:
                self.cache_path.write_text(cached)
            with self.assertLogs(level="WARNING") as logged:
                await package.comfy_entrypoint()
            self.assertIn("use custom", logged.output[0])
            options = n.OpenRouterText.INPUT_TYPES()["required"]["model"][1]["options"]
            self.assertEqual([option["key"] for option in options], ["custom"])

    async def test_cache_write_failure_does_not_lose_live_models(self):
        with patch.object(catalog.tempfile, "NamedTemporaryFile", side_effect=PermissionError), self.assertLogs(level="WARNING"):
            await catalog.load()
        self.assertEqual(catalog.model_ids, ("vendor/new-model",))


if __name__ == "__main__":
    unittest.main()
