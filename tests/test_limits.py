import unittest
from unittest.mock import patch

import torch

from test_jev import api, n
import test_typesafe as direct_tests

image_part = direct_tests.image_part
from jev_under_test import decisions, limits, media


QUESTION = {"q": {"type": "noul", "instructions": "Is this red?"}}


class LimitTests(unittest.TestCase):
    def test_jev_and_clef_question_boundaries(self):
        routes = [("typesafe", model) for model in limits.TYPESAFE_JEV]
        routes += [("openrouter", model) for model in (*limits.OPENROUTER_JEV, "cloudflare/clef", "cloudflare/clef-flash")]
        for provider, model in routes:
            for kind, maximum in (("choice", 255), ("score", 10)):
                for count in (maximum, maximum + 1):
                    criteria = {str(i): None for i in range(count)} if kind == "choice" else [str(i) for i in range(count)]
                    questions = {"q": {"type": kind, "instructions": "Compare", "criteria": criteria}}
                    with self.subTest(provider=provider, model=model, kind=kind, count=count):
                        if count == maximum:
                            self.assertEqual(decisions.payload("text", questions, model, provider)["questions"], questions)
                        else:
                            with self.assertRaisesRegex(ValueError, str(maximum)):
                                decisions.payload("text", questions, model, provider)

    def test_unknown_and_luna_models_do_not_inherit_jev_rubric_limits(self):
        for model in ("vendor/new", "openai/gpt-6-luna-decisions"):
            questions = {"q": {"type": "score", "criteria": list(range(11))}}
            self.assertEqual(decisions.payload("text", questions, model, "openrouter")["questions"], questions)

    def test_jev_dimension_area_and_format_boundaries(self):
        for provider, model, minimum in (("typesafe", "jev-1.14.0", 280), ("openrouter", "typesafe/jev-1.14", 256)):
            for size, valid in (((280, 280), True), ((1568, 1568), True), ((minimum - 1, 400), False),
                                ((1569, 280), False), ((256, 307), provider == "openrouter"), ((256, 306), False)):
                context = media.Context("", (image_part(size=size),))
                with self.subTest(provider=provider, size=size):
                    if valid:
                        decisions.payload(context, QUESTION, model, provider)
                    else:
                        with self.assertRaisesRegex(ValueError, "pixels"):
                            decisions.payload(context, QUESTION, model, provider)
            for part in (image_part("WEBP"), image_part(animated=True)):
                with self.assertRaisesRegex(ValueError, "still PNG or JPEG"):
                    decisions.payload(media.Context("", (part,)), QUESTION, model, provider)

    def test_jev_file_size_boundary_without_large_fixture(self):
        info = decisions._image(image_part())
        for size, valid in ((limits.JEV_IMAGE_BYTES, True), (limits.JEV_IMAGE_BYTES + 1, False)):
            with patch.object(decisions, "_image", return_value={**info, "size_bytes": size}):
                if valid:
                    decisions.payload(media.Context("", (image_part(),)), QUESTION, "jev-preview", "typesafe")
                else:
                    with self.assertRaisesRegex(ValueError, "16 MiB"):
                        decisions.payload(media.Context("", (image_part(),)), QUESTION, "jev-preview", "typesafe")

    def test_text_and_image_request_byte_boundaries(self):
        for provider, model in (("typesafe", "jev-latest"), ("openrouter", "typesafe/jev-1.14")):
            body = decisions.payload("日本語", QUESTION, model, provider)
            size = len(decisions.encode_payload(body))
            with patch.object(limits, "JEV_REQUEST_BYTES", size):
                self.assertEqual(decisions.payload("日本語", QUESTION, model, provider), body)
            with patch.object(limits, "JEV_REQUEST_BYTES", size - 1), self.assertRaisesRegex(ValueError, "32 MiB"):
                decisions.payload("日本語", QUESTION, model, provider)

    def test_json_nesting_boundary_and_nonfinite_numbers(self):
        state = "leaf"
        for _ in range(64): state = [state]
        decisions.payload(state, QUESTION, "jev-latest", "typesafe")
        with self.assertRaisesRegex(ValueError, "64 nested"):
            decisions.payload([state], QUESTION, "jev-latest", "typesafe")
        with self.assertRaises(ValueError):
            decisions.payload({"bad": float("inf")}, QUESTION, "jev-latest", "typesafe")

    def test_additional_model_image_counts_and_patch_boundaries(self):
        info = decisions._image(image_part(size=(32, 32)))
        for model in ("cloudflare/clef-omni", "inception/mercury-decide", "liquid/d1", "liquid/d1-ultra"):
            count = limits.IMAGE_COUNTS[model]
            limits.images([info] * count, model, "openrouter")
            with self.assertRaisesRegex(ValueError, "images"):
                limits.images([info] * (count + 1), model, "openrouter")
        limits.images([{**info, "width": 3200, "height": 3200}], "liquid/d1", "openrouter")
        with self.assertRaisesRegex(ValueError, "patches"):
            limits.images([{**info, "width": 3201, "height": 3200}], "liquid/d1", "openrouter")

    def test_mercury_file_and_liquid_body_boundaries(self):
        info = decisions._image(image_part())
        limits.images([{**info, "size_bytes": limits.MERCURY_IMAGE_BYTES}], "inception/mercury-decide", "openrouter")
        with self.assertRaisesRegex(ValueError, "local 8 MB"):
            limits.images([{**info, "size_bytes": limits.MERCURY_IMAGE_BYTES + 1}], "inception/mercury-decide", "openrouter")
        limits.request({}, limits.LIQUID_REQUEST_BYTES, "liquid/d1", "openrouter", True)
        with self.assertRaisesRegex(ValueError, "4.5 MB"):
            limits.request({}, limits.LIQUID_REQUEST_BYTES + 1, "liquid/d1", "openrouter", True)

    def test_error_hints_never_echo_provider_secrets_or_pixels(self):
        secret = "private-key-and-image-content"
        for status, body, expected in ((400, '{"error":{"code":"max_tokens_exceeded","message":"'+secret+'"}}', "context limit"),
                                        (422, '{"error":{"message":"context_length_exceeded '+secret+'"}}', "context limit"),
                                        (413, secret, "Processed images"), (422, secret, "Choice/Score"), (500, secret, "Request failed")):
            hint = api.error_hint(status, body)
            self.assertIn(expected, hint)
            self.assertNotIn(secret, hint)


class LimitTransportTests(unittest.IsolatedAsyncioTestCase):
    setUp = direct_tests.TypeSafeTests.setUp

    async def test_invalid_inputs_never_reach_transport(self):
        images = torch.zeros((1, 64, 64, 3))
        with self.assertRaisesRegex(ValueError, "pixels"):
            await n.JevInterpret.execute("photo", "Red?", "boolean", {"model": "typesafe/jev-1.14"}, provider="openrouter", images=images)
        self.transport.assert_not_called()

    async def test_context_error_through_actual_transport_is_sanitized(self):
        self.response.status = 400
        self.response.text.return_value = '{"error":{"code":"max_tokens_exceeded","message":"direct-test-key private-pixels"}}'
        with self.assertRaisesRegex(RuntimeError, "context limit") as raised:
            await api.evaluate("text", QUESTION, "jev-latest", provider="typesafe")
        self.assertNotIn("direct-test-key", str(raised.exception))
        self.assertNotIn("private-pixels", str(raised.exception))
        self.assertEqual(self.session.post.call_count, 1)
