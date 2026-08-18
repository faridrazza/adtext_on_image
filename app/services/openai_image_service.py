"""Adapter around the OpenAI image-edit endpoint.

Everything model-specific lives here -- including which output sizes each model
family accepts -- so the controller never has to reason about model quirks.
"""

from __future__ import annotations

import base64
import binascii
import logging
import math
from dataclasses import dataclass, field

from openai import AsyncOpenAI, OpenAIError

from app.core.config import Settings
from app.core.errors import ConfigurationError, RenderingError

logger = logging.getLogger(__name__)

# Only the gpt-image-2 family accepts arbitrary WIDTHxHEIGHT sizes.
_FLEXIBLE_PREFIX = "gpt-image-2"
# Every other GPT image model is limited to these three.
_FIXED_SIZES: tuple[tuple[int, int], ...] = ((1024, 1024), (1536, 1024), (1024, 1536))
_DALLE2_SIZES: tuple[tuple[int, int], ...] = ((256, 256), (512, 512), (1024, 1024))

# gpt-image-2 constraints, per the image-generation guide.
_ALIGN = 16
_MIN_PIXELS = 655_360
_MAX_PIXELS = 8_294_400
_MAX_EDGE = 3840
_MIN_RATIO = 1 / 3
_MAX_RATIO = 3.0


@dataclass
class SizePlan:
    """The size we will ask the model for, and why it may differ."""

    width: int
    height: int
    native: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def size_param(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class RenderResult:
    image_bytes: bytes
    size_plan: SizePlan
    model: str


def _align_up(value: float) -> int:
    """Round up to a multiple of 16, never below 16."""
    return max(_ALIGN, math.ceil(value / _ALIGN) * _ALIGN)


def _align_down(value: float) -> int:
    """Round down to a multiple of 16, never below 16."""
    return max(_ALIGN, math.floor(value / _ALIGN) * _ALIGN)


def _closest_fixed(
    width: int, height: int, options: tuple[tuple[int, int], ...]
) -> tuple[int, int]:
    """Pick the allowed size closest to what was asked.

    Aspect ratio dominates, but ties are broken by total pixels -- otherwise a
    set of same-ratio options (dall-e-2 is all 1:1) would always return the
    smallest one regardless of the size requested.
    """
    target_ratio = width / height
    target_pixels = width * height
    return min(
        options,
        key=lambda s: (
            round(abs((s[0] / s[1]) - target_ratio), 4),
            abs(s[0] * s[1] - target_pixels),
        ),
    )


def plan_size(model: str, width: int, height: int) -> SizePlan:
    """Choose the best output size this model can actually produce.

    ``native`` is True when the model can emit the requested dimensions exactly,
    meaning no resampling is needed downstream.
    """
    if model.startswith("dall-e-2"):
        w, h = _closest_fixed(width, height, _DALLE2_SIZES)
        return SizePlan(w, h, native=(w, h) == (width, height), warnings=[
            f"Model '{model}' supports only {', '.join(f'{a}x{b}' for a, b in _DALLE2_SIZES)}; "
            f"rendering at {w}x{h} and resampling to {width}x{height}."
        ] if (w, h) != (width, height) else [])

    if not model.startswith(_FLEXIBLE_PREFIX):
        w, h = _closest_fixed(width, height, _FIXED_SIZES)
        warnings = []
        if (w, h) != (width, height):
            warnings.append(
                f"Model '{model}' supports only fixed output sizes; rendering at "
                f"{w}x{h} and resampling to the requested {width}x{height}. Use "
                "gpt-image-2 to render the requested size natively."
            )
        return SizePlan(w, h, native=(w, h) == (width, height), warnings=warnings)

    # --- gpt-image-2: arbitrary sizes within documented bounds --------------
    warnings: list[str] = []
    ratio = width / height
    if not (_MIN_RATIO <= ratio <= _MAX_RATIO):
        w, h = _closest_fixed(width, height, _FIXED_SIZES)
        warnings.append(
            f"Aspect ratio {ratio:.3f} is outside the supported 1:3-3:1 range; "
            f"rendering at {w}x{h} and resampling to {width}x{height}."
        )
        return SizePlan(w, h, native=False, warnings=warnings)

    # Align upward so any correction downstream is a downsample, which stays
    # sharp, rather than an upsample, which softens.
    w, h = _align_up(width), _align_up(height)

    # Grow until the pixel floor is met. Each pass is forced to change the
    # size, so the loop always terminates.
    while w * h < _MIN_PIXELS:
        factor = math.sqrt(_MIN_PIXELS / (w * h))
        grown = (_align_up(w * factor), _align_up(h * factor))
        w, h = grown if grown != (w, h) else (w + _ALIGN, h + _ALIGN)

    # Shrink until both the pixel ceiling and the max edge are respected.
    while w * h > _MAX_PIXELS or max(w, h) > _MAX_EDGE:
        factor = min(
            math.sqrt(_MAX_PIXELS / (w * h)) if w * h > _MAX_PIXELS else 1.0,
            _MAX_EDGE / max(w, h) if max(w, h) > _MAX_EDGE else 1.0,
        )
        shrunk = (_align_down(w * factor), _align_down(h * factor))
        w, h = shrunk if shrunk != (w, h) else (
            max(_ALIGN, w - _ALIGN),
            max(_ALIGN, h - _ALIGN),
        )

    native = (w, h) == (width, height)
    if not native:
        warnings.append(
            f"Requested {width}x{height} is not directly renderable (sizes must be "
            f"multiples of {_ALIGN} within the model's pixel limits); rendering at "
            f"{w}x{h} and resampling to {width}x{height}."
        )
    return SizePlan(w, h, native=native, warnings=warnings)


class OpenAIImageService:
    """Renders the ad text and CTA onto an image via the edit endpoint."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not set; the image model cannot be reached."
            )
        self._model = settings.openai_image_model
        self._quality = settings.openai_image_quality
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    @property
    def model(self) -> str:
        return self._model

    async def render(
        self,
        *,
        image_png: bytes,
        prompt: str,
        width: int,
        height: int,
    ) -> RenderResult:
        plan = plan_size(self._model, width, height)

        try:
            # Deliberately no `response_format`: it is a dall-e-2-only parameter
            # and passing it to a GPT image model triggers a silent fallback.
            # GPT image models always return base64.
            response = await self._client.images.edit(
                model=self._model,
                image=("source.png", image_png, "image/png"),
                prompt=prompt,
                size=plan.size_param,
                output_format="png",
                quality=self._quality,
            )
        except OpenAIError as exc:
            logger.exception("Image edit request failed")
            raise RenderingError(
                f"The image model request failed: {exc}"
            ) from exc

        if not response.data or not response.data[0].b64_json:
            raise RenderingError("The image model returned no image data.")

        try:
            image_bytes = base64.b64decode(response.data[0].b64_json, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RenderingError(
                "The image model returned malformed image data."
            ) from exc

        return RenderResult(
            image_bytes=image_bytes, size_plan=plan, model=self._model
        )
