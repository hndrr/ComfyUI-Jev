"""OpenRouter Decisions contracts, with all HTTP traffic mocked."""

import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from test_jev import api, n, response_for, s


MODELS = ("openai/gpt-6-luna-decisions", "cloudflare/clef-flash", "cloudflare/clef")


class DecisionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []

        def post(endpoint, **kwargs):
            self.requests.append((endpoint, kwargs))
            payload = kwargs["json"]
            result = response_for(payload["questions"])
            result.update(model=payload["model"], id="mock-decisions", provider="mock")
            response = MagicMock(status=200)
            response.text = AsyncMock(return_value=s.dumps(result))
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            return response

        session = MagicMock()
        session.post.side_effect = post
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        for patcher in (
            patch.object(api.aiohttp, "ClientSession", return_value=session),
            patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-router-key"}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_presets_preserve_existing_defaults_and_custom_input(self):
        for node in (n.JevInterpret, n.JevSkillChoice):
            inputs = node.INPUT_TYPES()["required"]
            options = inputs["model"][1]["options"]
            self.assertEqual([option["key"] for option in options],
                             ["jev-latest", "jev-preview", "jev-1.13.0", *MODELS, "custom"])
            self.assertIn("model_id", options[-1]["inputs"]["required"])
            self.assertEqual(inputs["provider"][1]["default"], "typesafe")

    async def test_all_interpret_tasks_use_decisions_contract(self):
        for model in MODELS:
            for task, expected, kinds in (
                ("boolean", "true", ["noul"]),
                ("choice", "soft\nlight", ["choice"]),
                ("score", "0.5", ["score"]),
                ("multi_choice", '["soft\\nlight", "hard light"]', ["noul", "noul"]),
                ("extract", "8", ["choice"]),
            ):
                with self.subTest(model=model, task=task):
                    state = "Choose soft light for 8 seconds at 24 fps."
                    output = await n.JevInterpret.execute(
                        state, "Choose for this request", task, {"model": model},
                        provider="openrouter", candidates={"candidate0": "soft\nlight", "candidate1": "hard light"},
                    )
                    if task == "multi_choice":
                        self.assertEqual(json.loads(output.result[0]), json.loads(expected))
                    else:
                        self.assertEqual(output.result[0], expected)
                    endpoint, kwargs = self.requests[-1]
                    self.assertEqual(endpoint, "https://openrouter.ai/api/alpha/decisions")
                    payload = kwargs["json"]
                    self.assertEqual(set(payload), {"model", "state", "questions"})
                    self.assertEqual(payload["model"], model)
                    self.assertEqual(payload["state"], state)
                    self.assertEqual([q["type"] for q in payload["questions"].values()], kinds)
                    if task == "boolean":
                        self.assertEqual(payload["questions"], {
                            "f0": {"type": "noul", "instructions": "Choose for this request"},
                        })
                    self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test-router-key"})
                    self.assertFalse(kwargs["allow_redirects"])
                    raw = json.loads(output.result[2])
                    self.assertEqual(raw["model"], model)
                    self.assertEqual(raw["id"], "mock-decisions")
                    self.assertEqual(output.result[1]["usage"], raw["usage"])

    async def test_suggestions_and_skill_scoring_keep_structured_questions(self):
        records = [{"name": "Lighting", "path": "/mock/lighting/SKILL.md",
                    "description": "Photography lighting", "content": "Use soft window light."},
                   {"name": "Composition", "path": "/mock/composition/SKILL.md",
                    "description": "Photography composition", "content": "Frame the bottle tightly."}]
        for model in MODELS:
            self.requests.clear()
            output = await n.JevInterpret.execute(
                "Make a perfume photo", "Choose guidance", "suggest", {"model": model},
                provider="openrouter", candidates={"candidate0": "Use soft window light.", "candidate1": "Use hard light."},
            )
            self.assertEqual(json.loads(output.result[0]), ["Use soft window light."])
            self.assertEqual(len(self.requests), 2)
            with patch.object(n.skills, "read_skills", return_value=records):
                output = await n.JevSkillChoice.execute(
                    "Make a perfume photo", {"directory": "automatic"}, "Choose guidance",
                    {"model": model}, provider="openrouter", strength_mode="automatic",
                )
            self.assertEqual(len(self.requests), 4)
            selected = output.result[1]["skills"]
            self.assertEqual(selected[0]["content"], records[0]["content"])
            self.assertEqual(selected[0]["strength"], 1.0)
            questions = self.requests[-1][1]["json"]["questions"]
            self.assertEqual(questions["fits_c0"]["type"], "noul")
            self.assertIsInstance(questions["fits_c0"]["instructions"], dict)
            self.assertEqual(questions["applicability_c0"]["type"], "score")

    async def test_provider_pairing_and_text_misuse_fail_before_network(self):
        for model in MODELS:
            for provider in ("typesafe", "openai"):
                with self.assertRaises(ValueError):
                    await api.evaluate("request", {"q": {"type": "noul", "instructions": "?"}}, model, provider)
            for count in (0, 2):
                with self.assertRaisesRegex(ValueError, "do not generate text"):
                    await api.generate("Write text", "", f" {model} ", candidate_count=count)
        self.assertEqual(self.requests, [])

    async def test_custom_model_id_and_provider_key_isolation(self):
        for model in MODELS:
            output = await n.JevInterpret.execute(
                "question?", "Is it a question?", "boolean", {"model": "custom", "model_id": model},
                provider="openrouter", api_key="test-node-key",
            )
            self.assertEqual(output.result[0], "true")
            self.assertEqual(self.requests[-1][1]["headers"]["Authorization"], "Bearer test-node-key")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-wrong-provider-key"}, clear=True):
                with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                    await api.evaluate("request", {"q": {}}, model, "openrouter")

    async def test_luna_limit_counts_expanded_questions_and_presence_checks(self):
        schema = {"options": {"type": "multi_choice", "instructions": "Which apply?",
                              "presence": "explicit", "criteria": {f"c{i}": str(i) for i in range(199)}}}
        questions, _ = s.compile_questions("request", schema)
        self.assertEqual(len(questions), 200)
        await api.evaluate("request", questions, MODELS[0], "openrouter")
        self.assertEqual(len(self.requests), 1)
        schema["options"]["criteria"]["c199"] = "199"
        questions, _ = s.compile_questions("request", schema)
        self.assertEqual(len(questions), 201)
        with self.assertRaisesRegex(ValueError, "at most 200"):
            await api.evaluate("request", questions, MODELS[0], "openrouter")
        self.assertEqual(len(self.requests), 1)
        # These model-specific preflights do not change legacy/custom behavior.
        for model in ("jev-latest", "vendor/custom-model"):
            await api.evaluate("request", questions, model, "openrouter")
        self.assertEqual(len(self.requests), 3)
        self.assertEqual((await api.evaluate("", {}, MODELS[0], "openrouter"))["answers"], {})
        self.assertEqual(len(self.requests), 3)

    async def test_clef_conservative_upstream_limit_counts_expanded_questions(self):
        for model in MODELS[1:]:
            for candidates in (63, 64, 65):
                self.requests.clear()
                schema = {"options": {"type": "multi_choice", "instructions": "Which apply?",
                                      "criteria": {f"c{i}": str(i) for i in range(candidates)}}}
                questions, _ = s.compile_questions("request", schema)
                if candidates <= 64:
                    await api.evaluate("request", questions, model, "openrouter")
                    self.assertEqual(len(self.requests), 1)
                    self.assertEqual(len(self.requests[0][1]["json"]["questions"]), candidates)
                else:
                    with self.assertRaisesRegex(ValueError, "64 questions"):
                        await api.evaluate("request", questions, model, "openrouter")
                    self.assertEqual(self.requests, [])

    def test_refusals_and_incomplete_answers_never_become_judgments(self):
        for kind, criteria in (("noul", None), ("choice", {"a": "A", "b": "B"}),
                               ("score", ["Low", "High"])):
            with self.assertRaisesRegex(ValueError, "refused"):
                s._answer({"q": {"type": "refusal"}}, "q", kind, "test", criteria)
            with self.assertRaises(ValueError):
                s._answer({}, "q", kind, "test", criteria)
            if kind != "noul":
                for probabilities in (None, {}, {"unexpected": 1.0}):
                    answer = {"type": kind, "choice": "a", "score": 0.5,
                              "confidence": 0.9, "probabilities": probabilities}
                    with self.assertRaisesRegex(ValueError, "probability"):
                        s._answer({"q": answer}, "q", kind, "test", criteria)

    async def test_clef_criteria_limits_and_legacy_isolation(self):
        for model in MODELS[1:]:
            for kind, counts in (("choice", (1, 2, 255, 256)), ("score", (1, 2, 10, 11))):
                for count in counts:
                    self.requests.clear()
                    values = [str(i) for i in range(count)]
                    criteria = {str(i): value for i, value in enumerate(values)} if kind == "choice" else values
                    questions = {"q": {"type": kind, "instructions": "Which?", "criteria": criteria}}
                    if count in counts[1:3]:
                        await api.evaluate("request", questions, model, "openrouter")
                        self.assertEqual(len(self.requests), 1)
                    else:
                        with self.assertRaisesRegex(ValueError, "requires.*criteria"):
                            await api.evaluate("request", questions, model, "openrouter")
                        self.assertEqual(self.requests, [])
                        for legacy in ("jev-latest", "vendor/custom-model"):
                            await api.evaluate("request", questions, legacy, "openrouter")
                        self.assertEqual(len(self.requests), 2)


if __name__ == "__main__":
    unittest.main()
