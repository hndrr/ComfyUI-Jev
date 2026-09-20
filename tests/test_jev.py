import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
COMFY = ROOT.parents[1]
sys.path.insert(0, str(COMFY))
spec = importlib.util.spec_from_file_location("jev_under_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from jev_under_test import api, semantics as s, nodes as n


def response_for(questions):
    answers = {}
    for key, question in questions.items():
        kind = question["type"]
        if kind == "noul":
            answer = {"type": kind, "noul": 0.8}
        elif kind == "choice":
            choices = list(question["criteria"])
            answer = {"type": kind, "choice": choices[0], "confidence": 0.9,
                      "probabilities": {choice: float(i == 0) for i, choice in enumerate(choices)}}
        else:
            levels = question["criteria"]
            answer = {"type": kind, "score": (len(levels) - 1) / 2, "confidence": 0.7,
                      "legend": {str(i): value for i, value in enumerate(levels)},
                      "probabilities": {str(i): 1 / len(levels) for i in range(len(levels))}}
        answers[key] = answer
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 100, "output_tokens": 10}}


def completion_for(content):
    return {"model": "openai/gpt-4.1-mini", "choices": [
        {"finish_reason": "stop", "message": {"role": "assistant", "content": content}},
    ], "usage": {"prompt_tokens": 100, "completion_tokens": 100}}


def judged(schema, state="a request"):
    questions, plans = s.compile_questions(state, schema)
    return s.collect_judgments(response_for(questions), plans)


class SemanticsTests(unittest.TestCase):
    def test_all_field_types_and_bindings(self):
        schema = {
            "style": {"type": "choice", "instructions": "Style?", "criteria": {"soft": "Soft light", "hard": "Hard light"}},
            "layers": {"type": "multi_choice", "instructions": "Which layers?", "criteria": {"a": "Texture", "b": "Grain"}},
            "flag": {"type": "boolean", "instructions": "Add grain?"},
            "motion": {"type": "score", "instructions": "Motion?", "criteria": ["Still", "Some", "Strong"]},
            "duration": {"type": "extract", "instructions": "Duration?"},
        }
        questions, plans = s.compile_questions("長さは8秒、24fps", schema)
        self.assertEqual(len(questions), 6)
        self.assertEqual([v["text"] for v in plans["duration"]["candidates"].values()], ["8", "24"])
        judgments = s.collect_judgments(response_for(questions), plans)
        bindings = {"style": {"values": {"soft": {"prompt": "soft light"}, "hard": "hard light"}},
                    "layers": {"values": {"a": "cloth", "b": "film"}}, "flag": {"values": {"true": 1, "false": 0}},
                    "motion": {"range": [10, 20]}, "duration": {"convert": "int"}}
        result, values = s.resolve(judgments, bindings)
        self.assertEqual(values, {"style": {"prompt": "soft light"}, "layers": ["cloth", "film"], "flag": 1, "motion": 15, "duration": 8})
        self.assertEqual(s.pointer(values, "/style/prompt"), "soft light")
        self.assertEqual(judgments["fields"]["duration"]["source"]["start"], 3)


    def test_presence_threshold_default_and_null(self):
        schema = {"grain": {"type": "boolean", "instructions": "Add grain?", "presence": "explicit"}}
        questions, plans = s.compile_questions("nothing specified", schema)
        response = response_for(questions)
        response["answers"]["f0_present"]["noul"] = 0.2
        judgments = s.collect_judgments(response, plans)
        result, values = s.resolve(judgments, {})
        self.assertEqual(values, {})
        self.assertEqual(result["fields"]["grain"]["reason"], "not_explicit")
        result, values = s.resolve(judgments, {"grain": {"default": None}})
        self.assertEqual(values, {"grain": None})
        self.assertEqual(result["fields"]["grain"]["status"], "default")
        result, values = s.resolve(judgments, {"grain": {"presence_threshold": 0.2, "threshold": 0.8}})
        self.assertTrue(values["grain"])

    def test_low_confidence_does_not_mean_low_score(self):
        judgments = judged({"motion": {"type": "score", "instructions": "?", "criteria": ["Still", "Moving"]}})
        self.assertEqual(s.resolve(judgments, {})[1]["motion"], 0.5)
        self.assertEqual(s.resolve(judgments, {"motion": {"min_confidence": 0.8, "default": 0.1}})[1]["motion"], 0.1)

    def test_extraction_occurrences_and_none(self):
        schema = {"x": {"type": "extract", "instructions": "Final length?", "source": "/request"}}
        questions, plans = s.compile_questions({"request": "前は8秒、今も8秒"}, schema)
        self.assertEqual(len(plans["x"]["candidates"]), 2)
        response = response_for(questions)
        response["answers"]["f0"]["choice"] = "c1"
        value = s.collect_judgments(response, plans)
        self.assertEqual(value["fields"]["x"]["source"]["start"], 7)
        response["answers"]["f0"]["choice"] = "none"
        self.assertEqual(s.resolve(s.collect_judgments(response, plans), {})[1], {})
        questions, plans = s.compile_questions({"request": "短く"}, schema)
        self.assertEqual(questions, {})
        self.assertEqual(s.resolve(s.collect_judgments(response_for({}), plans), {})[1], {})

    def test_regex_and_numeric_conversion(self):
        schema = {"file": {"type": "extract", "instructions": "Which file?", "pattern": r'"([^"]+)"', "group": 1}}
        value = judged(schema, 'use "abc.png"')
        self.assertEqual(s.resolve(value, {})[1]["file"], "abc.png")
        self.assertEqual(s._convert_extracted("1e3", "int", "x"), 1000)
        self.assertEqual(s._convert_extracted("9007199254740993", "int", "x"), 9007199254740993)
        for text, kind in [("1.5", "int"), ("8秒", "float"), ("NaN", "float"), ("1e999", "float")]:
            with self.assertRaises(ValueError):
                s._convert_extracted(text, kind, "x")


    def test_bad_schema_and_response(self):
        for field in [{"type": "wrong", "instructions": "?"}, {"type": "score", "instructions": "?", "criteria": ["one"]},
                      {"type": "extract", "instructions": "?", "pattern": "["}, {"type": "extract", "instructions": "?", "group": 1},
                      {"type": "boolean", "instructions": "?", "typo": 1}]:
            with self.assertRaises(ValueError):
                s.validate_schema({"x": field})
        questions, plans = s.compile_questions("text", {"x": {"type": "choice", "instructions": "?", "criteria": {"a": None, "b": None}}})
        for answers in [{}, {"f0": {"type": "noul", "noul": 0.5}}]:
            with self.assertRaises(ValueError):
                s.collect_judgments({"answers": answers}, plans)
        response = response_for(questions)
        response["answers"]["f0"]["choice"] = "invented"
        with self.assertRaisesRegex(ValueError, "not a candidate"):
            s.collect_judgments(response, plans)



class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_and_exact_payload(self):
        responses = []
        for code in (429, 529, 200):
            response = MagicMock(status=code, headers={"Retry-After": "2"})
            response.read = AsyncMock(return_value=b"")
            response.text = AsyncMock(return_value='{"answers":{}}')
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            responses.append(response)
        session = MagicMock()
        session.post.side_effect = responses
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api.aiohttp, "ClientSession", return_value=session), patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-secret"}), patch.object(api.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            self.assertEqual(await api.evaluate("request", {"q": {"type": "noul", "instructions": "?"}}, "jev-latest"), {"answers": {}})
            self.assertEqual(session.post.call_count, 3)
            self.assertEqual(sleep.await_count, 2)
            call = session.post.call_args
            self.assertEqual(call.args[0], api.ENDPOINT)
            self.assertEqual(call.kwargs["json"]["state"], "request")
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer test-secret")
            self.assertFalse(call.kwargs["allow_redirects"])

    async def test_openrouter_payload_and_judgments(self):
        task = {"task": "choice", "candidates": {"candidate0": "soft\nwindow light", "candidate1": "hard sunlight"}}
        state = "quiet portrait"
        schema, _ = s.text_task("Choose lighting", task)
        questions, plans = s.compile_questions(state, schema)
        payload = response_for(questions)
        payload.update(model="typesafe/jev-1.13", provider="TypeSafe", id="mock-request")
        payload["usage"]["cost"] = 0.0001
        response = MagicMock(status=200)
        response.text = AsyncMock(return_value=json.dumps(payload))
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = response
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api.aiohttp, "ClientSession", return_value=session), patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "router-secret", "TYPESAFE_API_KEY": "direct-secret"}, clear=True
        ):
            output = await n.JevInterpret.execute(
                state, "Choose lighting", "choice", {"model": "jev-latest"},
                provider="openrouter", api_key="node-key", candidates=task["candidates"],
            )
        call = session.post.call_args
        self.assertEqual(call.args[0], "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(call.kwargs["json"], {"model": "~typesafe/jev-latest", "state": state, "questions": questions})
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer node-key")
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(output.result[0], "soft\nwindow light")
        self.assertEqual(json.loads(output.result[2]), payload)

    async def test_direct_key_precedence_and_blank_fallback(self):
        response = MagicMock(status=200)
        response.text = AsyncMock(return_value='{"answers":{}}')
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = response
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api.aiohttp, "ClientSession", return_value=session):
            for provider, env_name in (("typesafe", "TYPESAFE_API_KEY"), ("openrouter", "OPENROUTER_API_KEY")):
                for environment in ({}, {env_name: "environment-key"}):
                    with patch.dict(os.environ, environment, clear=True):
                        await api.evaluate("state", {"q": {}}, "jev-latest", provider, " direct-key ")
                        self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer direct-key")
                with patch.dict(os.environ, {env_name: "environment-key"}, clear=True):
                    await api.evaluate("state", {"q": {}}, "jev-latest", provider, "  ")
                    self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer environment-key")

    async def test_provider_validation_and_key_isolation(self):
        self.assertEqual(api.connection("openrouter", "jev-1.13.0")[2], "typesafe/jev-1.13")
        self.assertEqual(api.connection("openrouter", "typesafe/custom-version")[2], "typesafe/custom-version")
        with self.assertRaisesRegex(ValueError, "jev-preview"):
            api.connection("openrouter", "jev-preview")
        with self.assertRaisesRegex(ValueError, "Unknown Jev provider"):
            api.connection("invalid", "jev-latest")
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "direct-only"}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                await api.evaluate("", {"x": {}}, "jev-latest", "openrouter")
            self.assertEqual((await api.evaluate("", {}, "jev-latest", "openrouter"))["answers"], {})

    async def test_missing_key_and_no_questions(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual((await api.evaluate("", {}, "jev-latest"))["answers"], {})
            with self.assertRaisesRegex(ValueError, "TYPESAFE_API_KEY"):
                await api.evaluate("", {"x": {}}, "jev-latest")

    async def test_timeout_and_unauthorized_are_not_retried(self):
        for status in (401, 422, 500, 429):
            session = MagicMock()
            response = MagicMock(status=status, headers={})
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            response.read = AsyncMock()
            session.post.return_value = response
            session.__aenter__ = AsyncMock(return_value=session)
            session.__aexit__ = AsyncMock(return_value=False)
            with patch.object(api.aiohttp, "ClientSession", return_value=session), patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret"}), patch.object(api.asyncio, "sleep", new_callable=AsyncMock):
                with self.assertRaisesRegex(RuntimeError, f"HTTP {status}"):
                    await api.evaluate("", {"x": {}}, "jev-latest")
                self.assertEqual(session.post.call_count, 3 if status == 429 else 1)
        with patch.object(api.aiohttp, "ClientSession", side_effect=asyncio.TimeoutError), patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret"}):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                await api.evaluate("", {"x": {}}, "jev-latest")


class NodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_nodes_and_native_types(self):
        with patch.object(package.model_catalog, "load", new_callable=AsyncMock) as load:
            classes = await (await package.comfy_entrypoint()).get_node_list()
            load.assert_awaited_once()
        self.assertEqual(classes, [n.JevInterpret, n.OpenRouterText, n.JevSkillChoice])
        n.JevInterpret.INPUT_TYPES()
        self.assertEqual(n.JevInterpret.RETURN_TYPES, ["STRING", "DICT", "STRING"])
        n.OpenRouterText.INPUT_TYPES()
        self.assertEqual(n.OpenRouterText.RETURN_TYPES, ["STRING", "STRING"])
        n.JevSkillChoice.INPUT_TYPES()
        self.assertEqual(n.JevSkillChoice.RETURN_TYPES, ["STRING", "DICT", "DICT", "STRING"])
        self.assertNotIn("skill_strength", str(n.JevInterpret.INPUT_TYPES()))

    async def test_candidates_preserve_multiline_and_connection_order(self):
        first = " A prompt with spaces\n\nSecond paragraph. "
        task = {"task": "choice", "candidates": {"candidate1": "another", "candidate0": first},
                "candidates_json": json.dumps(["generated\ncandidate"])}
        async def fake(state, questions, model, **kwargs):
            self.assertEqual(questions["f0"]["criteria"], {"c0": first, "c1": "another", "c2": "generated\ncandidate"})
            return response_for(questions)
        with patch.object(api, "evaluate", side_effect=fake):
            output = await n.JevInterpret.execute("brief", "Choose", "choice", {"model": "jev-latest"},
                                                  candidates=task["candidates"], candidates_json=task["candidates_json"])
            self.assertEqual(output.result[0], first)
            self.assertEqual(output.result[1]["value"], first)

    async def test_all_tasks_without_schema_input(self):
        async def fake(state, questions, model, **kwargs):
            return response_for(questions)
        cases = [({"task": "multi_choice", "candidates_json": '["a", "b"]'}, '[\n  "a",\n  "b"\n]'),
                 ({"task": "boolean"}, "true"),
                 ({"task": "score", "candidates_json": '["low", "high"]'}, "0.5"),
                 ({"task": "extract"}, "8")]
        with patch.object(api, "evaluate", side_effect=fake):
            for task, expected in cases:
                output = await n.JevInterpret.execute("8 seconds", "Evaluate", task["task"], {"model": "jev-latest"},
                                                      candidates_json=task.get("candidates_json"))
                self.assertEqual(output.result[0], expected)
            with self.assertRaisesRegex(ValueError, "No source value"):
                await n.JevInterpret.execute("no number", "Duration", "extract", {"model": "jev-latest"})

    async def test_invalid_candidates(self):
        for candidate_json in ('[]', '["same", "same"]', '[""]', '[7]', '{}', 'not json'):
            with self.assertRaises(ValueError):
                s.text_task("Choose", {"task": "choice", "candidates_json": candidate_json})
        with self.assertRaises(ValueError):
            s.text_task("Score", {"task": "score", "candidates_json": '["one"]'})


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_request_and_candidate_to_jev_connection(self):
        candidates = ["Warm light\n\nNatural linen", "Cool light\nPolished metal"]
        payload = completion_for(json.dumps({"candidates": candidates}))
        response = MagicMock(status=200)
        response.text = AsyncMock(return_value=json.dumps(payload))
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = response
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api.aiohttp, "ClientSession", return_value=session), patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "shared-key"}, clear=True
        ):
            generated = await n.OpenRouterText.execute(
                "Write image prompts", "Be specific", {"output_mode": "candidates", "count": 2},
                {"model": "custom", "model_id": "vendor/my-model"},
            )
            call = session.post.call_args
            self.assertEqual(call.args[0], api.CHAT_ENDPOINT)
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer shared-key")
            self.assertFalse(call.kwargs["allow_redirects"])
            sent = call.kwargs["json"]
            self.assertEqual(sent["model"], "vendor/my-model")
            self.assertEqual(sent["messages"][0], {"role": "system", "content": "Be specific"})
            self.assertEqual(sent["messages"][-1], {"role": "user", "content": "Write image prompts"})
            self.assertTrue(sent["response_format"]["json_schema"]["strict"])
            self.assertTrue(sent["provider"]["require_parameters"])
            self.assertFalse(sent["stream"])
            self.assertEqual(json.loads(generated.result[0]), candidates)
            self.assertEqual(json.loads(generated.result[1]), payload)
            task = {"task": "choice", "candidates_json": generated.result[0]}
            questions, _ = s.compile_questions("warm and tactile", s.text_task("Choose", task)[0])
            response.text.return_value = json.dumps(response_for(questions))
            selected = await n.JevInterpret.execute("warm and tactile", "Choose", "choice",
                {"model": "jev-latest"}, provider="openrouter", candidates_json=task["candidates_json"])
            self.assertEqual(selected.result[0], candidates[0])
            self.assertEqual(session.post.call_args.kwargs["headers"]["Authorization"], "Bearer shared-key")

    async def test_plain_text_and_direct_key(self):
        content = "  Multi-line output\nwith whitespace preserved.  "
        response = MagicMock(status=200)
        response.text = AsyncMock(return_value=json.dumps(completion_for(content)))
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = response
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api.aiohttp, "ClientSession", return_value=session), patch.dict(
            os.environ, {"OPENROUTER_API_KEY": "environment-key"}, clear=True
        ):
            output = await n.OpenRouterText.execute("Write", "", {"output_mode": "text"},
                {"model": "openai/gpt-4.1-mini"}, api_key=" direct-key ")
        self.assertEqual(output.result[0], content)
        sent = session.post.call_args.kwargs
        self.assertEqual(sent["headers"]["Authorization"], "Bearer direct-key")
        self.assertNotIn("response_format", sent["json"])
        self.assertEqual(sent["json"]["messages"], [{"role": "user", "content": "Write"}])

    def test_incomplete_or_invalid_generations_fail_explicitly(self):
        malformed = [completion_for(""), {}, {"choices": [None]}]
        for reason in ("length", "content_filter", "tool_calls", None):
            response = completion_for("partial text")
            response["choices"][0]["finish_reason"] = reason
            malformed.append(response)
        refusal = completion_for("declined")
        refusal["choices"][0]["message"]["refusal"] = "refused"
        malformed.append(refusal)
        for response in malformed:
            with self.assertRaises(ValueError):
                api.generated_text(response)
        for content in ('not JSON', '[]', '{"candidates":["only one"]}',
                        '{"candidates":["same","same"]}', '{"candidates":["a",3]}',
                        '{"candidates":["a",""]}', '{"candidates":["a","b"],"extra":1}'):
            with self.assertRaises(ValueError):
                api.generated_text(completion_for(content), 2)

    async def test_missing_key_and_invalid_generation_inputs(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                await api.generate("prompt", "", "model")
        for prompt, model, count in (("", "model", 0), ("prompt", "", 0), ("prompt", "model", 1)):
            with self.assertRaises(ValueError):
                await api.generate(prompt, "", model, candidate_count=count)


if __name__ == "__main__":
    unittest.main()
