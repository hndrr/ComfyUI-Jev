import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from test_jev import api
import test_typesafe as direct_tests


QUESTION = {"q": {"type": "noul", "instructions": "Synthetic question"}}


class CredentialTests(unittest.IsolatedAsyncioTestCase):
    setUp = direct_tests.TypeSafeTests.setUp

    async def test_opposite_provider_manual_keys_are_blocked_before_transport(self):
        for provider, model, key in (("typesafe", "jev-latest", "router-test-key"),
                                      ("openrouter", "typesafe/jev-1.14", "direct-test-key")):
            with self.assertRaisesRegex(ValueError, "other provider") as raised:
                await api.evaluate("synthetic", QUESTION, model, provider, api_key=key)
            self.assertNotIn(key, str(raised.exception))
        self.transport.assert_not_called()

    async def test_openrouter_prefix_is_never_sent_to_typesafe(self):
        key = "sk-or-DUMMY-NOT-A-REAL-KEY"
        with self.assertRaisesRegex(ValueError, "other provider") as raised:
            await api.evaluate("synthetic", QUESTION, "jev-latest", "typesafe", api_key=key)
        self.assertNotIn(key, str(raised.exception))
        self.transport.assert_not_called()

    async def test_empty_widget_uses_only_the_selected_environment_key(self):
        for provider, model, key, endpoint in (("typesafe", "jev-latest", "direct-test-key", api.ENDPOINT),
                                                ("openrouter", "typesafe/jev-1.14", "router-test-key", api.OPENROUTER_ENDPOINT)):
            await api.evaluate("synthetic", QUESTION, model, provider, api_key="")
            call = self.session.post.call_args
            self.assertEqual(call.args[0], endpoint)
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer " + key)

    async def test_existing_unambiguous_manual_key_input_still_works(self):
        await api.evaluate("synthetic", QUESTION, "jev-latest", "typesafe", api_key="dummy-manual-direct")
        self.assertEqual(self.session.post.call_args.kwargs["headers"]["Authorization"], "Bearer dummy-manual-direct")

    async def test_endpoint_provider_or_key_variable_mismatch_cannot_send_credentials(self):
        for endpoint, variable, provider in ((api.OPENROUTER_ENDPOINT, "TYPESAFE_API_KEY", "typesafe"),
                                             (api.ENDPOINT, "OPENROUTER_API_KEY", "openrouter"),
                                             ("https://example.invalid", "TYPESAFE_API_KEY", "typesafe")):
            with self.assertRaisesRegex(ValueError, "must match"):
                await api._post_json(endpoint, {}, variable, "dummy", "Jev", provider)
        self.transport.assert_not_called()

    async def test_error_body_read_is_bounded_and_handles_invalid_utf8(self):
        self.response.status = 413
        self.response.text.reset_mock()
        self.response.content.read = AsyncMock(return_value=b"\xff" * (2 * api.ERROR_BODY_LIMIT))
        with self.assertRaisesRegex(RuntimeError, "size limit"):
            await api.evaluate("synthetic", QUESTION, "jev-latest", "typesafe")
        self.response.content.read.assert_awaited_once_with(api.ERROR_BODY_LIMIT)
        self.response.text.assert_not_awaited()
        self.assertLessEqual(len(await api.error_body(self.response)), api.ERROR_BODY_LIMIT)

    async def test_unreadable_error_body_retains_http_status_and_does_not_retry(self):
        self.response.status = 422
        for error in (api.aiohttp.ClientPayloadError("dummy broken body"), asyncio.TimeoutError(), UnicodeError("dummy encoding")):
            self.session.post.reset_mock()
            self.response.content.read = AsyncMock(side_effect=error)
            with self.assertRaisesRegex(RuntimeError, "HTTP 422") as raised:
                await api.evaluate("synthetic", QUESTION, "jev-latest", "typesafe")
            self.assertNotIn("dummy", str(raised.exception))
            self.assertEqual(self.session.post.call_count, 1)
