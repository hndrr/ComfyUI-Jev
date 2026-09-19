import asyncio
import copy
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
        self.assertEqual(s.read_value(result, "style", "/prompt", str), "soft light")
        self.assertEqual(judgments["fields"]["duration"]["source"]["start"], 3)

    def test_schema_builder_json_equivalence_and_duplicates(self):
        builder = s.make_field("style", "Style?", "choice", "infer", "soft\nhard")
        self.assertEqual(builder, s.merge_schemas([], s.dumps(builder)))
        self.assertEqual(builder, s.make_field("style", "Style?", "choice", "infer", '{"soft":null,"hard":null}', "json"))
        for operation in (lambda: s.merge_schemas([builder, builder]), lambda: s.loads('{"a":1,"a":2}'),
                          lambda: s.make_field("x", "?", "choice", "infer", "same\nsame"), lambda: s.loads("NaN")):
            with self.assertRaises(ValueError):
                operation()

    def test_presence_threshold_default_and_null(self):
        schema = s.make_field("grain", "Add grain?", "boolean", "explicit")
        questions, plans = s.compile_questions("nothing specified", schema)
        response = response_for(questions)
        response["answers"]["f0_present"]["noul"] = 0.2
        judgments = s.collect_judgments(response, plans)
        result, values = s.resolve(judgments, {})
        self.assertEqual(values, {})
        self.assertEqual(result["fields"]["grain"]["reason"], "not_explicit")
        with self.assertRaisesRegex(ValueError, "unresolved"):
            s.read_value(result, "grain", "", bool)
        result, values = s.resolve(judgments, {"grain": {"default": None}})
        self.assertEqual(values, {"grain": None})
        self.assertEqual(result["fields"]["grain"]["status"], "default")
        result, values = s.resolve(judgments, {"grain": {"presence_threshold": 0.2, "threshold": 0.8}})
        self.assertTrue(values["grain"])

    def test_low_confidence_does_not_mean_low_score(self):
        judgments = judged(s.make_field("motion", "?", "score", "infer", "Still\nMoving"))
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

    def test_strict_readers_and_pointer(self):
        result = {"fields": {"x": {"status": "resolved", "value": {"a/b": {"~": [7.0, True]}}}}}
        self.assertEqual(s.read_value(result, "x", "/a~1b/~0/0", int), 7)
        self.assertEqual(s.read_value(result, "x", "/a~1b/~0/0", float), 7.0)
        for path, expected in [("/a~1b/~0/1", int), ("/a~1b/~0/0", str), ("/missing", float), ("/a~2b", str)]:
            with self.assertRaises(ValueError):
                s.read_value(result, "x", path, expected)

    def test_bad_schema_and_response(self):
        for field in [{"type": "wrong", "instructions": "?"}, {"type": "score", "instructions": "?", "criteria": ["one"]},
                      {"type": "extract", "instructions": "?", "pattern": "["}, {"type": "extract", "instructions": "?", "group": 1},
                      {"type": "boolean", "instructions": "?", "typo": 1}]:
            with self.assertRaises(ValueError):
                s.validate_schema({"x": field})
        questions, plans = s.compile_questions("text", s.make_field("x", "?", "choice", "infer", "a\nb"))
        for answers in [{}, {"f0": {"type": "noul", "noul": 0.5}}]:
            with self.assertRaises(ValueError):
                s.collect_judgments({"answers": answers}, plans)
        response = response_for(questions)
        response["answers"]["f0"]["choice"] = "invented"
        with self.assertRaisesRegex(ValueError, "not a candidate"):
            s.collect_judgments(response, plans)

    def test_ranking_and_weighted_scores(self):
        judgments = judged({"a": {"type": "score", "instructions": "?", "criteria": ["low", "high"]},
                            "b": {"type": "boolean", "instructions": "?"},
                            "c": {"type": "score", "instructions": "?", "criteria": ["low", "medium", "high"]}})
        self.assertEqual([r["id"] for r in s.rank(judgments, ["c", "b", "a"])], ["b", "c", "a"])
        self.assertAlmostEqual(s.weighted_score(judgments, {"a": 1, "b": 3})[0], 0.725)
        with self.assertRaises(ValueError):
            s.weighted_score(judgments, {"a": 1, "b": -1})
        with self.assertRaises(ValueError):
            s.rank(judgments, [])


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
        schema = {
            "pick": {"type": "choice", "instructions": "Style?", "criteria": {"soft": None, "hard": None}},
            "flag": {"type": "boolean", "instructions": "Grain?"},
            "motion": {"type": "score", "instructions": "Motion?", "criteria": ["Still", "Strong"]},
            "duration": {"type": "extract", "instructions": "Duration?"},
        }
        state = {"brief": "8 seconds"}
        schema["duration"]["source"] = "/brief"
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
                json.dumps(state), "json", {"model": "jev-latest"}, 0,
                schema_json=json.dumps(schema), provider="openrouter", api_key="node-key",
            )
        call = session.post.call_args
        self.assertEqual(call.args[0], "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(call.kwargs["json"], {"model": "~typesafe/jev-latest", "state": state, "questions": questions})
        self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer node-key")
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertEqual(output.result[0], s.collect_judgments(payload, plans))
        self.assertEqual(json.loads(output.result[1]), payload)

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
    async def test_registration_and_schema(self):
        classes = await (await package.comfy_entrypoint()).get_node_list()
        self.assertEqual(len(classes), 11)
        for cls in classes:
            cls.INPUT_TYPES()
            self.assertTrue(cls.RETURN_TYPES)
        self.assertEqual(n.JevReadString.RETURN_TYPES, ["STRING", "COMBO"])

    async def test_autogrow_and_json_single_request(self):
        field = n.JevField.execute("duration", "Length?", "infer", {"kind": "extract", "source": "", "extractor": {"extractor": "number"}}).result[0]
        async def fake(state, questions, model, provider="typesafe", api_key=""):
            self.assertEqual(len(questions), 2)
            return response_for(questions)
        with patch.object(api, "evaluate", side_effect=fake) as transport:
            output = await n.JevInterpret.execute("8秒", "text", {"model": "jev-latest"}, 0,
                                                  {"schema0": field}, '{"flag":{"type":"boolean","instructions":"Add grain?"}}')
            resolved = n.JevResolve.execute(output.result[0], '{"duration":{"convert":"int"}}')
            self.assertEqual(n.JevReadInt.execute(resolved.result[0], "duration").result, (8,))
            n.JevResolve.execute(output.result[0], '{"duration":{"convert":"float"}}')
            self.assertEqual(transport.await_count, 1)

    async def test_model_list_fingerprint_and_allowlist(self):
        with patch.object(n.folder_paths, "get_filename_list", return_value=["a.safetensors", "sub/b.safetensors", "c.ckpt"]):
            output = n.JevModelCandidates.execute("loras", "*.safetensors", '{"a.safetensors":"soft"}').result[0]
            self.assertEqual(json.loads(output), {"a.safetensors": "soft", "sub/b.safetensors": None})
            first = n.JevModelCandidates.fingerprint_inputs("loras", "*", "{}")
        with patch.object(n.folder_paths, "get_filename_list", return_value=["new.safetensors"]):
            self.assertNotEqual(first, n.JevModelCandidates.fingerprint_inputs("loras", "*", "{}"))
        with self.assertRaises(ValueError):
            n.JevModelCandidates.execute("../../", "*", "{}")


if __name__ == "__main__":
    unittest.main()
