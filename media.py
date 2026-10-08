"""Normalize ComfyUI context without choosing a provider or fetching external data."""

import base64
from dataclasses import dataclass
from io import BytesIO
import json


@dataclass(frozen=True)
class Context:
    original: str
    parts: tuple


def prepare(original, content_json=None, images=None):
    if not isinstance(original, str):
        raise ValueError("state/prompt must be text")
    if not content_json and images is None:
        return original
    parts = [original]
    if content_json:
        try:
            supplied = json.loads(content_json)
        except (ValueError, TypeError):
            raise ValueError("content_json must be a JSON array of typed content parts") from None
        if not isinstance(supplied, list):
            raise ValueError("content_json must be a JSON array of typed content parts")
        for part in supplied:
            if not isinstance(part, dict):
                raise ValueError("Each content_json item must be a typed object")
            kind = part.get("type")
            if kind == "text":
                if set(part) != {"type", "text"} or not isinstance(part["text"], str):
                    raise ValueError("A text part requires only type and a text string")
                parts.append(part["text"])
            elif kind == "image_url":
                image = part.get("image_url")
                if (set(part) != {"type", "image_url"} or not isinstance(image, dict)
                        or not set(image) <= {"url", "detail"} or not isinstance(image.get("url"), str)):
                    raise ValueError("An image_url part requires an image_url object with url and optional detail")
                if "detail" in image and image["detail"] not in ("auto", "low", "high"):
                    raise ValueError("Image detail must be auto, low, or high")
                parts.append({"type": kind, "image_url": dict(image)})
            elif kind in ("input_audio", "audio_url", "video_url", "file"):
                parts.append(dict(part))
            else:
                raise ValueError("Unsupported content part; use text or image_url (not input_image)")
    if images is not None:
        import torch
        from PIL import Image

        if (not isinstance(images, torch.Tensor) or images.ndim != 4 or images.shape[0] == 0
                or min(images.shape[1:3]) == 0 or images.shape[-1] not in (1, 3, 4)):
            raise ValueError("images must be a nonempty ComfyUI IMAGE batch [batch, height, width, channels]")
        if not images.is_floating_point() or not bool(torch.isfinite(images).all()) or bool(((images < 0) | (images > 1)).any()):
            raise ValueError("IMAGE pixels must be finite floating point values between 0 and 1")
        for frame in images.detach().to(device="cpu", dtype=torch.float32):
            pixels = (frame.numpy() * 255).round().astype("uint8")
            if pixels.shape[-1] == 1:
                pixels = pixels[..., 0]
            output = BytesIO()
            Image.fromarray(pixels).save(output, format="PNG")
            url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return Context(original, tuple(parts))
