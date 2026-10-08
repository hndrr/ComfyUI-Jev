import base64
from io import BytesIO
import json
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image
import torch

from test_jev import api, n, response_for
from test_suggestions import CANDIDATES, reply
from jev_under_test import decisions, media, suggestions


def image_url(color="red"):
    output = BytesIO()
    Image.new("RGB", (8, 8), color).save(output, "PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def part(color="red"):
    return {"type": "image_url", "image_url": {"url": image_url(color)}}


QUESTION = {"q": {"type": "noul", "instructions": "Contains red?"}}


class MediaTests(unittest.TestCase):
    def test_legacy_original_is_unchanged(self):
        state = '{"request":"8 seconds"}\n'
        self.assertEqual(media.prepare(state), state)
        self.assertEqual(decisions.payload(state, QUESTION, "jev-latest", "typesafe"),
                         {"model": "jev-latest", "state": state, "questions": QUESTION})

    def test_order_and_entire_batch(self):
        batch = torch.zeros((2, 5, 7, 3))
        batch[0, :, :, 0] = 1
        batch[1, :, :, 2] = 1
        context = media.prepare("original", json.dumps([{"type": "text", "text": "extra"}, part("green")]), batch)
        self.assertEqual(context.original, "original")
        self.assertEqual(context.parts[:2], ("original", "extra"))
        pixels = []
        for item in context.parts[2:]:
            data = base64.b64decode(item["image_url"]["url"].split(",")[1])
            with Image.open(BytesIO(data)) as image:
                pixels.append((image.size, image.getpixel((0, 0))))
        self.assertEqual(pixels, [((8, 8), (0, 128, 0)), ((7, 5), (255, 0, 0)), ((7, 5), (0, 0, 255))])

    def test_bad_tensor_and_parts_are_rejected(self):
        for images in (torch.zeros((0, 2, 2, 3)), torch.zeros((2, 2, 3)), torch.full((1, 2, 2, 3), float("nan")), torch.full((1, 2, 2, 3), 1.1)):
            with self.subTest(shape=images.shape), self.assertRaises(ValueError):
                media.prepare("", images=images)
        for content in ('{}', '["text"]', '[{"type":"input_image"}]', '[{"type":"text","text":3}]'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                media.prepare("", content)

    def test_documented_wire_shape_and_provider_routes(self):
        context = media.prepare("original", json.dumps([{"type": "text", "text": "extra"}, part()]))
        for model, spec in decisions.MODELS.items():
            with self.subTest(model=model):
                body = decisions.payload(context, QUESTION, model, "openrouter")
                self.assertEqual(body["state"], ["original", "extra", part()])
                self.assertEqual(body["provider"], {"only": [spec["provider"]], "allow_fallbacks": False})
                self.assertNotIn("input_image", json.dumps(body))
                self.assertNotIn("images", body)

    def test_remote_urls_unsupported_modalities_and_wrong_provider_fail(self):
        for item in ({"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
                     {"type": "input_audio", "input_audio": {}}, {"type": "video_url", "video_url": {}}, {"type": "file", "file": {}}):
            context = media.prepare("", json.dumps([item]))
            with self.assertRaises(ValueError):
                decisions.payload(context, QUESTION, "openai/gpt-6-luna-decisions", "openrouter")
        context = media.prepare("", json.dumps([part()]))
        for provider, model in (("typesafe", "jev-latest"), ("typesafe", "openai/gpt-6-luna-decisions"), ("openrouter", "typesafe/jev-1.13")):
            with self.assertRaises(ValueError):
                decisions.payload(context, QUESTION, model, provider)

    def test_limits_reject_without_truncation(self):
        for model, spec in decisions.MODELS.items():
            with self.subTest(model=model):
                context = media.Context("", ("", *([part()] * spec["images"])))
                self.assertEqual(len(decisions.payload(context, QUESTION, model, "openrouter")["state"]), spec["images"] + 1)
                with self.assertRaisesRegex(ValueError, "images"):
                    decisions.payload(media.Context("", context.parts + (part(),)), QUESTION, model, "openrouter")
                with self.assertRaisesRegex(ValueError, "questions"):
                    decisions.payload("", {f"q{i}": QUESTION["q"] for i in range(spec["questions"] + 1)}, model, "openrouter")
        with self.assertRaisesRegex(ValueError, "levels"):
            decisions.payload("", {"q": {"type": "score", "criteria": list(range(11))}}, "cloudflare/clef", "openrouter")
        with patch.object(decisions.warnings, "warn") as warning:
            body = decisions.payload("x" * 2500, QUESTION, "cloudflare/clef-flash", "openrouter")
        self.assertIn("truncated upstream", warning.call_args.args[0])
        self.assertEqual(body["state"], "x" * 2500)


class NodeMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_candidate_retains_gate_fit_and_applicability_checks(self):
        for model in decisions.MODELS:
            for gated, fit, score in ((True, 0.9, None), (False, 0.1, None),
                                      (False, 0.9, None), (False, 0.9, 0), (False, 0.9, 4)):
                with self.subTest(model=model, gated=gated, fit=fit, score=score):
                    async def post(endpoint, payload, *args):
                        questions = payload["questions"]
                        self.assertNotIn("which", questions)
                        return reply(questions, gated=gated,
                                     fits={"c0": fit} if "fits_c0" in questions else {},
                                     scores={"c0": score} if "applicability_c0" in questions else {})
                    with patch.object(api, "_post_json", side_effect=post) as transport:
                        values, details, responses = await suggestions.suggest(
                            "request", "Choose", CANDIDATES[:1], model, "openrouter", "",
                            score_applicability=score is not None,
                        )
                    self.assertEqual(transport.await_count, 1 if gated else 2)
                    expected = [CANDIDATES[0]["value"]] if not gated and fit >= 0.5 and score != 0 else []
                    self.assertEqual(values, expected)
                    self.assertEqual(details["ranking"][0]["probability"], 1.0)
                    if score is not None and not gated:
                        self.assertEqual(details["applicability"]["c0"], score / 4)

    async def test_single_shortlist_skips_only_comparison_through_real_adapter(self):
        for model in decisions.MODELS:
            for fit in (0.1, 0.9):
                with self.subTest(model=model, fit=fit):
                    async def post(endpoint, payload, *args):
                        questions = payload["questions"]
                        if "gate_work" in questions:
                            self.assertEqual(len(questions["which"]["criteria"]), len(CANDIDATES))
                            return reply(questions)
                        self.assertEqual(set(questions), {"fits_c0"})
                        return reply(questions, fits={"c0": fit})
                    with patch.object(api, "_post_json", side_effect=post) as transport:
                        values, _, _ = await suggestions.suggest(
                            "request", "Choose", CANDIDATES, model, "openrouter", "", shortlist_size=1,
                        )
                    self.assertEqual(transport.await_count, 2)
                    self.assertEqual(values, [CANDIDATES[0]["value"]] if fit >= 0.5 else [])

    async def test_extraction_offsets_still_use_original_text(self):
        async def fake(state, questions, model, **kwargs):
            self.assertIsInstance(state, media.Context)
            self.assertEqual(state.original, "前は8秒")
            return response_for(questions)
        with patch.object(api, "evaluate", side_effect=fake):
            output = await n.JevInterpret.execute(
                "前は8秒", "Duration?", "extract", {"model": "openai/gpt-6-luna-decisions"},
                provider="openrouter", content_json=json.dumps([{"type": "text", "text": "Additional number 99"}, part()]),
            )
        self.assertEqual(output.result[0], "8")
        self.assertEqual(output.result[1]["judgment"]["source"]["start"], 2)

    async def test_suggest_reuses_complete_context_in_both_stages(self):
        context = media.prepare("request", json.dumps([part()]))
        records = [{"description": "one", "content": "first", "value": "a"}, {"description": "two", "content": "second", "value": "b"}]
        with patch.object(api, "evaluate", side_effect=lambda state, questions, *args, **kw: response_for(questions)) as transport:
            await suggestions.suggest(context, "Choose", records, "cloudflare/clef", "openrouter", "")
        self.assertEqual(transport.await_count, 2)
        self.assertTrue(all(call.args[0] is context for call in transport.call_args_list))

    async def test_skill_choice_receives_media_context(self):
        record = {"name": "A", "description": "one", "content": "complete", "path": "skill-a"}
        with patch.object(n.skills, "read_skills", return_value=[record]), patch.object(n.suggestions, "suggest", new_callable=AsyncMock, return_value=([], {"selected": [], "applicability": {}}, {})) as suggest:
            await n.JevSkillChoice.execute("brief", {"directory": "automatic"}, "Choose", {"model": "cloudflare/clef-flash"}, provider="openrouter", images=torch.zeros((1, 8, 8, 3)))
        self.assertIsInstance(suggest.call_args.args[0], media.Context)
        self.assertEqual(len(suggest.call_args.args[0].parts), 2)

    async def test_transport_receives_only_documented_image_payload(self):
        context = media.prepare("request", json.dumps([part()]))
        with patch.object(api, "_post_json", new_callable=AsyncMock, return_value={"answers": {}}) as post:
            await api.evaluate(context, QUESTION, "openai/gpt-6-luna-decisions", "openrouter")
        self.assertEqual(post.call_args.args[0], api.OPENROUTER_ENDPOINT)
        self.assertEqual(post.call_args.args[1]["state"], ["request", part()])


if __name__ == "__main__":
    unittest.main()
