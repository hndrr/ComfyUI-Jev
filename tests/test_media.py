"""Image encoding and candidate wire format; these are not live vision tests."""

import base64
from io import BytesIO
import json
import unittest
from unittest.mock import patch

import torch
from PIL import Image

from test_jev import api, n, response_for
from jev_under_test import media


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def test_png_batch_preserves_order_dimensions_and_pixels(self):
        images = torch.zeros((2, 3, 4, 3))
        images[0, :, :, 0] = 1
        images[1, :, :, 2] = 1
        original = images.clone()
        urls = media.image_data_urls(images)
        self.assertTrue(torch.equal(images, original))
        self.assertEqual(len(urls), 2)
        for url, expected in zip(urls, [(255, 0, 0), (0, 0, 255)]):
            self.assertTrue(url.startswith("data:image/png;base64,"))
            image = Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1])))
            self.assertEqual(image.size, (4, 3))
            self.assertEqual(image.getpixel((0, 0)), expected)
        for channels, mode in [(1, "L"), (4, "RGBA")]:
            url = media.image_data_urls(torch.ones((1, 2, 3, channels)))[0]
            image = Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1])))
            self.assertEqual(image.mode, mode)

    def test_invalid_batches_are_rejected(self):
        for images in ([], torch.zeros((2, 3, 3)), torch.zeros((0, 2, 3, 3)),
                       torch.zeros((1, 2, 3, 2)), torch.zeros((1, 2, 3, 3), dtype=torch.uint8),
                       torch.full((1, 2, 3, 3), float("nan")), torch.full((1, 2, 3, 3), float("inf"))):
            with self.subTest(shape=getattr(images, "shape", None)):
                with self.assertRaises(ValueError):
                    media.image_data_urls(images)

    async def test_explicit_opt_in_and_model_guard_before_http(self):
        images = torch.ones((1, 2, 3, 3))
        with patch.object(api, "_post_json") as transport:
            for model, provider, enabled in (
                (api.LUNA_DECISIONS_MODEL, "openrouter", False),
                (api.LUNA_DECISIONS_MODEL, "typesafe", True),
                ("cloudflare/clef", "openrouter", True),
                ("cloudflare/clef-flash", "openrouter", True),
                ("jev-latest", "openrouter", True),
            ):
                with self.assertRaisesRegex(ValueError, "experimental|Experimental"):
                    await n.JevInterpret.execute("text", "Which?", "boolean", {"model": model},
                                                 provider=provider, images=images, experimental_images=enabled)
            transport.assert_not_called()
        state = {"unchanged": ["JSON", "content"]}
        self.assertIs(media.decision_state(state, None, False, "jev-latest", "typesafe"), state)

    async def test_image_requests_use_candidate_state_and_keep_text_extraction(self):
        requests = []

        async def fake(endpoint, payload, key_name, api_key, label, provider):
            requests.append(payload)
            self.assertEqual(endpoint, api.OPENROUTER_ENDPOINT)
            return response_for(payload["questions"])

        images = torch.zeros((2, 2, 3, 3))
        text = "長さは8秒、24fps"
        with patch.object(api, "_post_json", side_effect=fake):
            output = await n.JevInterpret.execute(
                text, "Duration?", "extract", {"model": api.LUNA_DECISIONS_MODEL},
                provider="openrouter", images=images, experimental_images=True,
            )
        self.assertEqual(output.result[0], "8")
        self.assertEqual(output.result[1]["judgment"]["source"]["start"], 3)
        payload = requests[0]
        self.assertEqual(set(payload), {"model", "state", "questions"})
        self.assertEqual(payload["state"], [{"role": "user", "content": [
            {"type": "input_text", "text": text},
            *[{"type": "input_image", "image_url": url} for url in media.image_data_urls(images)],
        ]}])
        self.assertEqual(json.loads(payload["questions"]["f0"]["criteria"]["c0"])["text"], "8")

    async def test_images_reach_both_suggestion_stages_and_skill_choice(self):
        requests = []

        async def fake(state, questions, model, **kwargs):
            requests.append(state)
            return response_for(questions)

        images = torch.zeros((1, 2, 3, 3))
        opts = dict(model={"model": api.LUNA_DECISIONS_MODEL}, provider="openrouter",
                    images=images, experimental_images=True)
        records = [{"name": "Lighting", "description": "Lighting", "content": "Use soft light.",
                    "path": "/mock/lighting/SKILL.md"}]
        with patch.object(api, "evaluate", side_effect=fake), patch.object(n.skills, "read_skills", return_value=records):
            await n.JevInterpret.execute("Photo", "Choose guidance", "suggest",
                                         candidates={"candidate0": "Use soft light."}, **opts)
            await n.JevSkillChoice.execute("Photo", {"directory": "automatic"}, "Choose guidance", **opts)
        self.assertEqual(len(requests), 4)
        expected = media.decision_state("Photo", images, True, api.LUNA_DECISIONS_MODEL, "openrouter")
        self.assertTrue(all(state == expected for state in requests))


if __name__ == "__main__":
    unittest.main()
