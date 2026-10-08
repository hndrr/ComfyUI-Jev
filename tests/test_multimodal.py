import base64
from io import BytesIO
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image
import torch

from test_jev import api, n, response_for
from test_suggestions import CANDIDATES, reply
from jev_under_test import decisions, media, suggestions


def image_url(color="red", format="PNG"):
    output = BytesIO()
    Image.new("RGB", (8, 8), color).save(output, format)
    return f"data:image/{format.lower()};base64," + base64.b64encode(output.getvalue()).decode()


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
        body = decisions.payload("x" * 2500, QUESTION, "cloudflare/clef-flash", "openrouter")
        self.assertEqual(body["state"], "x" * 2500)

    def test_png_jpeg_webp_are_preserved(self):
        for format in ("PNG", "JPEG", "WEBP"):
            item = {"type": "image_url", "image_url": {"url": image_url(format=format)}}
            for model in decisions.MODELS:
                with self.subTest(format=format, model=model):
                    body = decisions.payload(media.prepare("", json.dumps([item])), QUESTION, model, "openrouter")
                    self.assertEqual(body["state"], ["", item])

    def test_declared_mime_must_match_decoded_format(self):
        for format, declared in (("PNG", "jpeg"), ("JPEG", "webp"), ("WEBP", "png")):
            url = image_url(format=format).replace(f"image/{format.lower()}", f"image/{declared}")
            context = media.prepare("", json.dumps([{"type": "image_url", "image_url": {"url": url}}]))
            with self.subTest(format=format), self.assertRaisesRegex(ValueError, "declared MIME"):
                decisions.payload(context, QUESTION, "cloudflare/clef", "openrouter")

    def test_invalid_and_truncated_images_are_rejected(self):
        urls = ["data:image/png;base64,not-base64!", "data:image/png;base64," + base64.b64encode(b"not an image").decode()]
        for format in ("PNG", "JPEG", "WEBP"):
            header, encoded = image_url(format=format).split(",")
            data = base64.b64decode(encoded)
            # Keep enough JPEG structure for Image.open/verify to succeed;
            # loading its pixels must still reject the missing end of the file.
            data = data[:-2] if format == "JPEG" else data[:len(data) // 2]
            urls.append(header + "," + base64.b64encode(data).decode())
        for url in urls:
            context = media.prepare("", json.dumps([{"type": "image_url", "image_url": {"url": url}}]))
            with self.subTest(header=url[:30]), self.assertRaisesRegex(ValueError, "Invalid or oversized image"):
                decisions.payload(context, QUESTION, "cloudflare/clef", "openrouter")

    def test_budget_counts_unicode_escaping(self):
        state = "日" * 50000
        body = {"model": "cloudflare/clef", "state": state, "questions": QUESTION}
        self.assertLess(len(json.dumps(body, ensure_ascii=False).encode("utf-8")), 256 * 1024)
        self.assertGreater(len(json.dumps(body).encode("utf-8")), 256 * 1024)
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            decisions.payload(state, QUESTION, "cloudflare/clef", "openrouter")

    def test_budget_boundary_includes_provider(self):
        for model in ("cloudflare/clef", "cloudflare/clef-flash"):
            for images in (False, True):
                def context(text):
                    return media.Context(text, (text, part())) if images else text
                body = decisions.payload(context("日"), QUESTION, model, "openrouter")
                text = "日" + "x" * (256 * 1024 - len(json.dumps(body).encode("utf-8")))
                with self.subTest(model=model, images=images):
                    body = decisions.payload(context(text), QUESTION, model, "openrouter")
                    self.assertEqual(len(json.dumps(body).encode("utf-8")), 256 * 1024)
                    if images:
                        self.assertEqual(body["provider"], {"only": ["cloudflare"], "allow_fallbacks": False})
                    with self.assertRaisesRegex(ValueError, "256 KiB"):
                        decisions.payload(context(text + "x"), QUESTION, model, "openrouter")

    def test_text_warning_threshold_counts_utf8_and_all_text_parts(self):
        for model in ("cloudflare/clef", "cloudflare/clef-flash"):
            self.assertEqual(decisions.context_warnings("x" * 2000, model, "openrouter"), [])
            context = media.prepare("日" * 500, json.dumps([{"type": "text", "text": "日" * 200}, part()]))
            notice = decisions.context_warnings(context, model, "openrouter")
            self.assertEqual(len(notice), 1)
            self.assertIn("not a token count or proof of truncation", notice[0])
        for provider, model in (("openrouter", "openai/gpt-6-luna-decisions"), ("typesafe", "jev-latest")):
            self.assertEqual(decisions.context_warnings("x" * 3000, model, provider), [])


class NodeMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_interpret_warning_is_present_on_every_result(self):
        with patch.object(api, "evaluate", side_effect=lambda state, questions, *args, **kw: response_for(questions)):
            for refresh in (0, 1):
                output = await n.JevInterpret.execute("日" * 700, "Red?", "boolean", {"model": "cloudflare/clef"}, provider="openrouter", refresh=refresh)
                self.assertEqual(len(output.result[1]["warnings"]), 1)
                self.assertNotIn("warnings", json.loads(output.result[2]))
            short = await n.JevInterpret.execute("short", "Red?", "boolean", {"model": "cloudflare/clef"}, provider="openrouter")
            self.assertNotIn("warnings", short.result[1])

    async def test_suggest_warning_is_present_on_every_result_including_gate_exit(self):
        for gated in (False, True):
            with patch.object(api, "evaluate", side_effect=lambda state, questions, *args, **kw: reply(questions, gated=gated)):
                for refresh in (0, 1):
                    output = await n.JevInterpret.execute("x" * 2001, "Choose", "suggest", {"model": "cloudflare/clef-flash"}, provider="openrouter", candidates_json=json.dumps(["a", "b"]), refresh=refresh)
                    self.assertEqual(len(output.result[1]["warnings"]), 1)
                    self.assertEqual(output.result[1]["reason"], "skill_not_needed" if gated else None)

    async def test_skill_warning_is_present_on_every_result(self):
        record = {"name": "A", "description": "one", "content": "complete", "path": "skill-a"}
        with patch.object(n.skills, "read_skills", return_value=[record]), patch.object(api, "evaluate", side_effect=lambda state, questions, *args, **kw: reply(questions)):
            for refresh in (0, 1):
                output = await n.JevSkillChoice.execute("x" * 2001, {"directory": "automatic"}, "Choose", {"model": "cloudflare/clef"}, provider="openrouter", refresh=refresh)
                self.assertEqual(len(output.result[2]["warnings"]), 1)
        with patch.object(api, "evaluate") as evaluate:
            _, details, _ = await suggestions.suggest("x" * 2001, "Choose", [], "cloudflare/clef", "openrouter", "")
        self.assertNotIn("warnings", details)
        evaluate.assert_not_called()

    async def test_transmitted_bytes_match_final_budget_with_non_ascii_and_image(self):
        model = "cloudflare/clef"
        initial = decisions.payload(media.Context("", ("日", part())), QUESTION, model, "openrouter")
        state = "日" + "x" * (256 * 1024 - len(json.dumps(initial).encode("utf-8")))
        context = media.Context(state, (state, part()))
        response = MagicMock(status=200)
        response.text = AsyncMock(return_value='{"answers":{}}')
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = response
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api.aiohttp, "ClientSession", return_value=session):
            await api.evaluate(context, QUESTION, model, "openrouter", "test-key")
            sent = session.post.call_args.kwargs
            self.assertIsInstance(sent["data"], bytes)
            self.assertEqual(len(sent["data"]), 256 * 1024)
            self.assertEqual(json.loads(sent["data"]), {"model": model, "state": [state, part()], "questions": QUESTION, "provider": {"only": ["cloudflare"], "allow_fallbacks": False}})
            self.assertIn(b"\\u65e5", sent["data"])
            self.assertEqual(sent["headers"]["Content-Type"], "application/json")
            self.assertNotIn("json", sent)
            session.post.reset_mock()
            with self.assertRaisesRegex(ValueError, "256 KiB"):
                await api.evaluate(media.Context(state, (state + "x", part())), QUESTION, model, "openrouter", "test-key")
            session.post.assert_not_called()

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
