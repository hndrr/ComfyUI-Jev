"""Selected-frame Box image preparation and local rectangular regions."""

import hashlib
import math

import numpy as np
from PIL import Image
import torch

from . import media, semantics as s


def selected_image(images, image_index):
    if (not isinstance(images, torch.Tensor) or images.ndim != 4 or images.shape[0] == 0
            or min(images.shape[1:3]) == 0 or images.shape[-1] not in (1, 3, 4)):
        raise ValueError("images must be a nonempty ComfyUI IMAGE batch [batch, height, width, channels]")
    if type(image_index) is not int or not 0 <= image_index < images.shape[0]:
        raise ValueError(f"image_index must be between 0 and {images.shape[0] - 1}")
    frame = images[image_index:image_index + 1]
    if not frame.is_floating_point() or not bool(torch.isfinite(frame).all()) or bool(((frame < 0) | (frame > 1)).any()):
        raise ValueError("IMAGE pixels must be finite floating point values between 0 and 1")
    return frame


def image_fingerprint(frame):
    pixels = frame.detach().to(device="cpu").contiguous()
    digest = hashlib.sha256(str(pixels.dtype).encode("ascii"))
    digest.update(pixels.view(torch.uint8).numpy())
    return digest.hexdigest()


def context(frame, provider="openrouter"):
    """Resize only the upload copy; normalized edges still address the original."""
    _, height, width, _ = frame.shape
    minimum = 280 if provider == "typesafe" else 256
    lower = max(minimum / min(width, height), math.sqrt(78400 / (width * height)))
    upper = 1568 / max(width, height)
    if lower > upper:
        raise ValueError("Jev cannot read this extreme aspect ratio; crop or pad the image before Jev Find Box")
    scale = min(upper, max(1.0, lower))
    upload_width = min(1568, math.ceil(width * scale))
    upload_height = min(1568, math.ceil(height * scale))
    if (upload_width, upload_height) != (width, height):
        pixels = (frame[0].detach().to(device="cpu", dtype=torch.float32).numpy() * 255).round().astype("uint8")
        if pixels.shape[-1] == 1:
            pixels = pixels[..., 0]
        resized = Image.fromarray(pixels).resize((upload_width, upload_height), Image.Resampling.LANCZOS)
        # Use the existing encoder and provider adapter for the transport.
        pixels = np.array(resized, copy=True)
        if pixels.ndim == 2:
            pixels = pixels[..., None]
        frame = torch.from_numpy(pixels).to(dtype=torch.float32).unsqueeze(0) / 255
    return media.prepare("Locate the described target in this image.", images=frame), {
        "width": upload_width, "height": upload_height,
    }


def region(images, box_data, padding=0, on_missing="error"):
    if not isinstance(box_data, dict) or type(box_data.get("found")) is not bool:
        raise ValueError("Connect box_data from Jev Find Box")
    frame = selected_image(images, box_data.get("image_index"))
    _, height, width, channels = frame.shape
    if (box_data.get("width"), box_data.get("height"), box_data.get("batch_size")) != (width, height, images.shape[0]):
        raise ValueError("Box source size or batch changed; connect the same images to both Box nodes")
    if box_data.get("source_hash") != image_fingerprint(frame):
        raise ValueError("Selected image changed; connect the same images to both Box nodes and rerun Jev Find Box")
    if type(padding) is not int or padding < 0:
        raise ValueError("padding must be a nonnegative number of original-image pixels")
    if on_missing not in ("error", "keep_image"):
        raise ValueError("on_missing must be error or keep_image")
    mask = frame.new_zeros((1, height, width))
    preview = frame.clone()
    details = {**box_data, "padding": padding, "pixels": None, "effective_box": None}
    if not box_data["found"]:
        if on_missing == "error":
            raise ValueError(f"Jev did not accept a target box ({box_data.get('reason')}); inspect box_data or use on_missing=keep_image for an unchanged image and empty mask")
        return frame.clone(), mask, preview, details
    box = s.validate_box(box_data.get("box"))
    left = max(0, math.floor(box["left"] * width) - padding)
    top = max(0, math.floor(box["top"] * height) - padding)
    right = min(width, math.ceil(box["right"] * width) + padding)
    bottom = min(height, math.ceil(box["bottom"] * height) + padding)
    mask[:, top:bottom, left:right] = 1
    line = min(3, right - left, bottom - top)
    color = frame.new_tensor([1.0] if channels == 1 else [1.0, 0.1, 0.1])
    color_channels = min(3, channels)
    preview[:, top:top + line, left:right, :color_channels] = color
    preview[:, bottom - line:bottom, left:right, :color_channels] = color
    preview[:, top:bottom, left:left + line, :color_channels] = color
    preview[:, top:bottom, right - line:right, :color_channels] = color
    details["pixels"] = {"left": left, "top": top, "right": right, "bottom": bottom,
                         "width": right - left, "height": bottom - top}
    details["effective_box"] = {"left": left / width, "top": top / height,
                                "right": right / width, "bottom": bottom / height}
    return frame[:, top:bottom, left:right, :].clone(), mask, preview, details
