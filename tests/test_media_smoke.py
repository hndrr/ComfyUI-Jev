"""Check the optional live test's controls without making live requests."""

import importlib.util
import copy
from io import StringIO
import unittest
from unittest.mock import MagicMock, patch

from test_jev import ROOT

spec = importlib.util.spec_from_file_location("luna_smoke", ROOT / "tools/check_luna_images.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def answers(expected, confidence):
    return {"answers": {f"slot{i}": {"type": "choice", "choice": color,
            "probabilities": {key: confidence if key == color else (1 - confidence) / 5 for key in smoke.COLORS}}
            for i, color in enumerate(expected)}}


class SmokeTests(unittest.TestCase):
    def test_control_contains_no_image_and_candidate_uses_same_questions(self):
        expected, control, candidate = smoke.prepare()
        self.assertEqual(len(expected), 6)
        self.assertIsInstance(control["state"], str)
        self.assertEqual(candidate["questions"], control["questions"])
        content = candidate["state"][0]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": control["state"]})
        self.assertEqual(content[1]["type"], "input_image")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))

    def test_correct_pixels_must_beat_negative_control(self):
        expected = list(smoke.COLORS)
        control = answers(expected, 1 / 6)
        correct = answers(expected, 0.99)
        self.assertTrue(smoke.assess(expected, control, correct)[0])
        self.assertFalse(smoke.assess(expected, correct, correct)[0])
        self.assertFalse(smoke.assess(expected, control, control)[0])
        correct["answers"]["slot0"]["choice"] = "blue"
        self.assertFalse(smoke.assess(expected, control, correct)[0])
        with self.assertRaises(ValueError):
            smoke.assess(expected, control, {"answers": {}})

    def test_malformed_responses_cannot_pass(self):
        expected = list(smoke.COLORS)
        good = answers(expected, 0.99)
        bad = [None, [], "text", 1, {}, {"answers": None}, {"answers": []},
               {"answers": {"slot0": None}}, {"answers": {"slot0": []}}]
        for replacement in ({key: 0.99 for key in smoke.COLORS}, {key: 0 for key in smoke.COLORS},
                            {key: float("nan") for key in smoke.COLORS}, {key: True for key in smoke.COLORS}, [], None):
            response = copy.deepcopy(good)
            response["answers"]["slot0"]["probabilities"] = replacement
            bad.append(response)
        for choice in (None, [], "invented"):
            response = copy.deepcopy(good)
            response["answers"]["slot0"]["choice"] = choice
            bad.append(response)
        for response in bad:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    smoke.assess(expected, good, response)
                with self.assertRaises(ValueError):
                    smoke.assess(expected, response, good)
        rounded_control = answers(expected, 1 / 6)
        for answer in rounded_control["answers"].values():
            answer["probabilities"] = {key: 0.17 for key in smoke.COLORS}
        self.assertTrue(smoke.assess(expected, rounded_control, good)[0])

    def test_default_mode_never_reads_credentials_or_calls_network(self):
        environment = MagicMock()
        environment.environ.get.side_effect = AssertionError("credential access")
        with patch.object(smoke, "os", environment), \
             patch.object(smoke, "send", side_effect=AssertionError("network")), \
             patch("sys.argv", ["check_luna_images.py"]), patch("sys.stdout", new_callable=StringIO):
            self.assertEqual(smoke.main(), 0)
