"""Opt-in two-request vision check. Without --run, no credentials or network.

Uses a synthetic random-color image and a text-only negative control. A mocked
response, HTTP 200, or client-valid JSON cannot establish visual decoding.
"""

import argparse
import base64
from io import BytesIO
import json
import os
import secrets
import urllib.error
import urllib.request

from PIL import Image, ImageDraw


MODEL = "openai/gpt-6-luna-decisions"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
COLORS = {"red": "#f02020", "green": "#20b030", "blue": "#2040f0", "yellow": "#f0e020",
          "purple": "#a020c0", "orange": "#f09020"}


def prepare():
    expected = [secrets.choice(tuple(COLORS)) for _ in range(6)]
    image = Image.new("RGB", (900, 180), "white")
    draw = ImageDraw.Draw(image)
    for index, color in enumerate(expected):
        draw.ellipse((index * 150 + 20, 30, index * 150 + 130, 140), fill=COLORS[color])
    with BytesIO() as buffer:
        image.save(buffer, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    text = "The image contains six colored circles in one horizontal row. Judge each circle's color."
    questions = {f"slot{i}": {"type": "choice", "instructions": f"What color is circle {i + 1}, counting from the left?",
                               "criteria": {key: key.capitalize() for key in COLORS}} for i in range(6)}
    control = {"model": MODEL, "state": text, "questions": questions}
    candidate = {"model": MODEL, "state": [{"role": "user", "content": [
        {"type": "input_text", "text": text}, {"type": "input_image", "image_url": data_url},
    ]}], "questions": questions}
    return expected, control, candidate


def send(payload, api_key):
    request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    # Do not forward the Authorization header to redirected endpoints.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"OpenRouter HTTP {error.code}; no retry performed") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("OpenRouter connection failed; no retry performed") from None


def assess(expected, control, candidate):
    """Strict evidence check; inconclusive results do not prove incompatibility."""
    for response in (control, candidate):
        if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
            raise ValueError("Response must contain an answers object; verification inconclusive")
    rows = []
    for index, color in enumerate(expected):
        key = f"slot{index}"
        answers = []
        for response in (control, candidate):
            answer = response["answers"].get(key)
            if not isinstance(answer, dict) or answer.get("type") != "choice":
                raise ValueError(f"{key}: missing choice answer; verification inconclusive")
            probabilities = answer.get("probabilities")
            if not isinstance(probabilities, dict) or set(probabilities) != set(COLORS):
                raise ValueError(f"{key}: missing choice distribution; verification inconclusive")
            if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1 for p in probabilities.values()):
                raise ValueError(f"{key}: invalid probabilities; verification inconclusive")
            # Six values rounded to two decimals may accumulate up to 0.03 error.
            if abs(sum(probabilities.values()) - 1) > len(COLORS) * 0.005 + 1e-9:
                raise ValueError(f"{key}: probabilities must sum to one; verification inconclusive")
            if not isinstance(answer.get("choice"), str) or answer["choice"] not in COLORS:
                raise ValueError(f"{key}: choice is not a candidate; verification inconclusive")
            answers.append((answer.get("choice"), probabilities[color]))
        (baseline_choice, baseline), (choice, probability) = answers
        rows.append({"slot": index + 1, "expected": color, "image_choice": choice,
                     "image_probability": probability, "control_choice": baseline_choice,
                     "control_probability": baseline})
    passed = all(row["image_choice"] == row["expected"] and row["image_probability"] >= 0.8 for row in rows)
    passed = passed and sum(row["image_probability"] - row["control_probability"] >= 0.5 for row in rows) >= 4
    return passed, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize up to TWO paid OpenRouter requests using OPENROUTER_API_KEY")
    args = parser.parse_args()
    expected, control, candidate = prepare()
    if not args.run:
        print("Prepared six random colors, a synthetic image, and a text-only control. No network or credentials accessed.")
        print("Use --run only when you authorize up to two paid requests. No retries; each request contains six questions.")
        return 0
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        parser.error("Set OPENROUTER_API_KEY in this process environment before --run")
    try:
        control_response = send(control, api_key)
        image_response = send(candidate, api_key)
        passed, rows = assess(expected, control_response, image_response)
        print(json.dumps({"passed": passed, "model": image_response.get("model"), "results": rows}, indent=2))
        print("Visual-decoding evidence observed for this request." if passed else "Inconclusive: do not claim image support from these results.")
        return 0 if passed else 1
    except (RuntimeError, ValueError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
