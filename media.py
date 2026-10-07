"""ComfyUI image encoding and the opt-in OpenRouter media transport candidate.

The router SDK preserves structured state. Server-side interpretation of OpenAI
content parts inside that state has not been verified; see docs/modalities.md.
"""

import base64
from io import BytesIO

from .api import LUNA_DECISIONS_MODEL


def image_data_urls(images):
    """Encode every ComfyUI IMAGE batch item as PNG, preserving batch order."""
    import torch
    from PIL import Image

    if not isinstance(images, torch.Tensor) or images.ndim != 4:
        raise ValueError("images must be a ComfyUI IMAGE tensor [batch, height, width, channels]")
    if any(size == 0 for size in images.shape) or images.shape[-1] not in (1, 3, 4):
        raise ValueError("images must contain nonempty grayscale, RGB, or RGBA frames")
    pixels = images.detach().cpu()
    if not pixels.is_floating_point() or not torch.isfinite(pixels).all().item():
        raise ValueError("images must contain finite floating-point pixels")
    pixels = pixels.clamp(0, 1).mul(255).round().to(torch.uint8).numpy()
    urls = []
    for frame in pixels:
        if frame.shape[-1] == 1:
            frame = frame[:, :, 0]
        with BytesIO() as buffer:
            Image.fromarray(frame).save(buffer, format="PNG")
            urls.append("data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"))
    return urls


def decision_state(text, images, experimental_images, model, provider):
    """Keep text unchanged; explicitly opt into an unverified media wire format."""
    if images is None:
        return text
    if not experimental_images:
        raise ValueError("Image transport is experimental: enable experimental_images to try the OpenRouter state mapping; server image decoding is not verified")
    if provider != "openrouter" or model.strip() != LUNA_DECISIONS_MODEL:
        raise ValueError("Experimental image transport currently requires provider=openrouter and openai/gpt-6-luna-decisions")
    if not isinstance(text, str):
        raise ValueError("Image state must have text context")
    content = [{"type": "input_text", "text": text}]
    content.extend({"type": "input_image", "image_url": url} for url in image_data_urls(images))
    return [{"role": "user", "content": content}]
