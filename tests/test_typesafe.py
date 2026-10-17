import base64
from io import BytesIO
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
import torch

from test_jev import api, n, response_for, s
from jev_under_test import boxes, decisions, media


def image_part(format="PNG", size=(280, 280), animated=False):
    output = BytesIO()
    image = Image.new("RGB", size, "red")
    options = {"save_all": True, "append_images": [Image.new("RGB", size, "blue")], "duration": 100} if animated else {}
    image.save(output, format, **options)
    return {"type": "image_url", "image_url": {"url": f"data:image/{format.lower()};base64," + base64.b64encode(output.getvalue()).decode()}}


def reply(presence=0.95):
    return {"model": "jev-1.14.0", "answers": {
        "target": {"type": "box", "box": {"left": 0.2, "top": 0.3, "right": 0.8, "bottom": 0.9}, "confidence": 0.9},
        "present": {"type": "noul", "noul": presence},
    }}


class TypeSafeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.response = MagicMock(status=200)
        self.response.text = AsyncMock(return_value=s.dumps(reply()))
        self.response.content.read = AsyncMock(side_effect=lambda count: self.response.text.return_value.encode()[:count])
        self.response.__aenter__ = AsyncMock(return_value=self.response)
        self.response.__aexit__ = AsyncMock(return_value=False)
        self.session = MagicMock()
        self.session.post.return_value = self.response
        self.session.__aenter__ = AsyncMock(return_value=self.session)
        self.session.__aexit__ = AsyncMock(return_value=False)
        patcher = patch.object(api.aiohttp, "ClientSession", return_value=self.session)
        self.transport = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {"TYPESAFE_API_KEY": "direct-test-key", "OPENROUTER_API_KEY": "router-test-key"}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_direct_box_sends_assets_header_models_and_selected_frame(self):
        images = torch.zeros((2, 280, 280, 3))
        images[1] = 0.4
        for model in decisions.TYPESAFE_BOX_MODELS:
            with self.subTest(model=model):
                output = await n.JevFindBox.execute(images, "the mug", image_index=1, model=model, provider="typesafe")
                call = self.session.post.call_args
                body = json.loads(call.kwargs["data"])
                self.assertEqual(call.args[0], api.ENDPOINT)
                self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer direct-test-key")
                self.assertEqual(call.kwargs["headers"]["TypeSafe-Danger-Zone-Images"], "true")
                self.assertFalse(call.kwargs["allow_redirects"])
                self.assertEqual(set(body), {"model", "state", "questions", "assets"})
                self.assertEqual(body["model"], model)
                self.assertEqual(len(body["assets"]), 1)
                asset = body["assets"][body["state"][-1]]
                self.assertEqual(set(asset), {"type", "format", "data_base64"})
                self.assertEqual((asset["type"], asset["format"]), ("image", "png"))
                self.assertFalse(asset["data_base64"].startswith("data:"))
                with Image.open(BytesIO(base64.b64decode(asset["data_base64"]))) as image:
                    self.assertEqual(image.size, (280, 280))
                    self.assertEqual(image.getpixel((0, 0)), (102, 102, 102))
                self.assertEqual([question["type"] for question in body["questions"].values()], ["box", "noul"])
                self.assertNotIn("criteria", body["questions"]["target"])
                self.assertTrue(output.result[1])
                self.assertEqual(output.result[0]["provider"], "typesafe")
                self.assertEqual(output.result[0]["image_index"], 1)

    async def test_direct_absence_reuses_presence_gate_and_zero_mask(self):
        self.response.text.return_value = s.dumps(reply(presence=0.01))
        images = torch.zeros((1, 280, 280, 3))
        output = await n.JevFindBox.execute(images, "the mug", model="jev-1.14.0", provider="typesafe")
        self.assertFalse(output.result[1])
        data = output.result[0]
        with self.assertRaisesRegex(ValueError, "did not accept"):
            boxes.region(images, data)
        crop, mask, preview, details = boxes.region(images, data, on_missing="keep_image")
        self.assertTrue(torch.equal(crop, images))
        self.assertTrue(torch.equal(preview, images))
        self.assertEqual(mask.sum().item(), 0)
        self.assertIsNone(details["pixels"])

    async def test_provider_credentials_are_isolated(self):
        images = torch.zeros((1, 280, 280, 3))
        for available, provider, model, missing in (
            ({"OPENROUTER_API_KEY": "router-only"}, "typesafe", "jev-1.14.0", "TYPESAFE_API_KEY"),
            ({"TYPESAFE_API_KEY": "direct-only"}, "openrouter", "typesafe/jev-1.14", "OPENROUTER_API_KEY"),
        ):
            with patch.dict(os.environ, available, clear=True), self.assertRaisesRegex(ValueError, missing):
                await n.JevFindBox.execute(images, "the mug", model=model, provider=provider)
        self.transport.assert_not_called()

    async def test_openrouter_default_does_not_inherit_direct_headers_or_assets(self):
        images = torch.zeros((1, 280, 280, 3))
        await n.JevFindBox.execute(images, "the mug", model="jev-preview", provider="typesafe")
        output = await n.JevFindBox.execute(images, "the mug")
        call = self.session.post.call_args
        body = json.loads(call.kwargs["data"])
        self.assertEqual(call.args[0], api.OPENROUTER_ENDPOINT)
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer router-test-key")
        self.assertNotIn("TypeSafe-Danger-Zone-Images", call.kwargs["headers"])
        self.assertNotIn("assets", body)
        self.assertEqual(body["state"][1]["type"], "image_url")
        self.assertEqual(output.result[0]["provider"], "openrouter")

    async def test_wrong_provider_model_pairs_fail_before_transport(self):
        images = torch.zeros((1, 280, 280, 3))
        for provider, model in (("typesafe", "jev-latest"), ("typesafe", "jev-1.13.0"),
                                ("typesafe", "typesafe/jev-1.14"), ("openrouter", "jev-1.14.0"), ("invalid", "jev-preview")):
            with self.subTest(provider=provider, model=model), self.assertRaises(ValueError):
                await n.JevFindBox.execute(images, "the mug", model=model, provider=provider)
        self.transport.assert_not_called()
        for model in decisions.TYPESAFE_BOX_MODELS:
            with self.assertRaisesRegex(ValueError, "requires an image"):
                decisions.payload("no image", s.box_questions("the mug"), model, "typesafe")

    def test_direct_minimum_dimensions_resize_only_the_upload(self):
        frame = torch.zeros((1, 512, 256, 3))
        _, direct = boxes.context(frame, "typesafe")
        _, router = boxes.context(frame, "openrouter")
        self.assertEqual(direct, {"width": 280, "height": 560})
        self.assertEqual(router, {"width": 256, "height": 512})
        self.assertEqual(frame.shape, (1, 512, 256, 3))
        with self.assertRaisesRegex(ValueError, "aspect ratio"):
            boxes.context(torch.zeros((1, 256, 1568, 3)), "typesafe")

    def test_invalid_direct_assets_are_rejected(self):
        invalid = [image_part("WEBP"), image_part(size=(279, 300)), image_part(size=(1569, 280)),
                   image_part(animated=True), {"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64,not-base64!"}}]
        for part in invalid:
            with self.subTest(url=part["image_url"]["url"][:35]), self.assertRaises(ValueError):
                decisions.payload(media.Context("photo", ("photo", part)), s.box_questions("the mug"), "jev-1.14.0", "typesafe")

    def test_assets_preserve_order_and_unique_references(self):
        part = image_part()
        context = media.Context("original", ("original", part, "second", part))
        body = decisions.payload(context, s.box_questions("the mug"), "jev-preview", "typesafe")
        self.assertEqual(body["state"][::2], ["original", "second"])
        self.assertEqual(len(body["assets"]), 2)
        self.assertNotEqual(body["state"][1], body["state"][3])
        self.assertEqual(set(body["state"][1::2]), set(body["assets"]))
        jpeg = decisions.payload(media.Context("", (image_part("JPEG"),)), s.box_questions("the mug"), "jev-preview", "typesafe")
        self.assertEqual(next(iter(jpeg["assets"].values()))["format"], "jpeg")

    async def test_text_only_direct_requests_do_not_enable_images(self):
        self.response.text.return_value = s.dumps(response_for({"q": {"type": "noul"}}))
        questions = {"q": {"type": "noul", "instructions": "Relevant?"}}
        await api.evaluate("text", questions, "jev-latest", provider="typesafe")
        call = self.session.post.call_args
        self.assertNotIn("TypeSafe-Danger-Zone-Images", call.kwargs["headers"])
        self.assertEqual(json.loads(call.kwargs["data"]), {"model": "jev-latest", "state": "text", "questions": questions})

    def test_direct_request_budget_uses_transmitted_bytes(self):
        context = media.Context("日", ("日", image_part()))
        with patch.object(decisions.uuid, "uuid4", return_value="unique-reference"):
            body = decisions.payload(context, s.box_questions("the mug"), "jev-preview", "typesafe")
            size = len(decisions.encode_payload(body))
            with patch.object(decisions, "TYPESAFE_REQUEST_BUDGET", size):
                self.assertEqual(decisions.payload(context, s.box_questions("the mug"), "jev-preview", "typesafe"), body)
            with patch.object(decisions, "TYPESAFE_REQUEST_BUDGET", size - 1), self.assertRaisesRegex(ValueError, "32 MiB"):
                decisions.payload(context, s.box_questions("the mug"), "jev-preview", "typesafe")

    async def test_existing_interpret_image_input_uses_direct_assets(self):
        self.response.text.return_value = s.dumps(response_for({"f0": {"type": "noul"}}))
        result = await n.JevInterpret.execute("photo", "Is there a mug?", "boolean", {"model": "jev-latest"},
                                              provider="typesafe", images=torch.zeros((1, 280, 280, 3)))
        self.assertEqual(result.result[0], "true")
        self.assertIn("assets", json.loads(self.session.post.call_args.kwargs["data"]))
        self.assertEqual(self.session.post.call_args.kwargs["headers"]["TypeSafe-Danger-Zone-Images"], "true")

    def test_provider_is_appended_to_preserve_saved_widget_positions(self):
        schema = n.JevFindBox.INPUT_TYPES()
        names = list(schema["required"]) + list(schema["optional"])
        self.assertEqual(names, ["images", "target", "image_index", "model", "api_key", "refresh", "presence_threshold", "min_confidence", "provider"])
        self.assertEqual(schema["optional"]["provider"][1]["default"], "openrouter")


if __name__ == "__main__":
    unittest.main()
