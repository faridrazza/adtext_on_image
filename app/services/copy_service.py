"""Stage 1: decide what the ad should say.

A text model reads the photograph, the source text and the placement, and
returns structured copy. Whatever it returns is then checked deterministically
here -- the model's own claim that it followed the rules is not evidence.
"""

from __future__ import annotations

import base64
import logging
import re
from enum import Enum

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.errors import ConfigurationError, CopyGenerationError
from app.domain.platforms import AssetSpec
from app.services import prompt_service

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2

# Phrases that turn a headline into a call-to-action, which this product does
# not produce. Checked deterministically because it is a hard product rule.
_CTA_PHRASES = (
    "book now", "book today", "call now", "call today", "call us",
    "shop now", "order now", "buy now", "learn more", "find out more",
    "contact us", "get a quote", "request a quote", "sign up", "subscribe now",
    "click here", "visit us", "get started", "apply now", "download now",
)

# Any digit run, percentage or currency amount is a factual claim.
_NUMERIC = re.compile(r"\d[\d,.]*\s?%?|[$€£]\s?\d[\d,.]*")


class Placement(str, Enum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class AdCopy(BaseModel):
    """The words to set, plus where they should sit."""

    headline: str = Field(description="The headline, distilled from the brief.")
    subheadline: str | None = Field(
        description="Optional supporting line, or null."
    )
    placement: Placement = Field(
        description="Region of the photograph with room for the text."
    )
    source_support: str = Field(
        description=(
            "The fragment of the brief that makes the headline true. Used for "
            "auditing, never rendered."
        )
    )


def _words(text: str) -> int:
    return len(text.split()) if text and text.strip() else 0


def _digits(text: str) -> set[str]:
    """Numeric claims, normalised so 1,000 and 1000 compare equal."""
    return {m.group().replace(",", "").replace(" ", "").rstrip(".")
            for m in _NUMERIC.finditer(text)}


def check_copy(copy: AdCopy, source_text: str, spec: AssetSpec) -> list[str]:
    """Deterministic policy check. Returns a list of violations, empty if clean."""
    headline_words, support_words = prompt_service.word_budget(spec)
    problems: list[str] = []

    if not copy.headline.strip():
        problems.append("The headline is empty.")

    if _words(copy.headline) > headline_words:
        problems.append(
            f"The headline is {_words(copy.headline)} words; the limit for "
            f"{spec.label} is {headline_words}. Distil it further."
        )

    if copy.subheadline:
        if support_words == 0:
            problems.append(
                f"{spec.label} takes no supporting line; return null for it."
            )
        elif _words(copy.subheadline) > support_words:
            problems.append(
                f"The supporting line is {_words(copy.subheadline)} words; the "
                f"limit is {support_words}."
            )

    written = f"{copy.headline} {copy.subheadline or ''}".lower()

    for phrase in _CTA_PHRASES:
        if phrase in written:
            problems.append(
                f"'{phrase}' is a call-to-action. Write the message, not the "
                "button."
            )
            break

    invented = _digits(written) - _digits(source_text.lower())
    if invented:
        problems.append(
            f"These figures do not appear in the brief: {sorted(invented)}. "
            "Every number must come from the brief."
        )

    return problems


class CopyService:
    """Writes the ad copy, then verifies it before anything is rendered."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not set; the copy model cannot be reached."
            )
        self._model = settings.openai_text_model
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )

    @property
    def model(self) -> str:
        return self._model

    async def write(
        self,
        *,
        image_png: bytes,
        source_text: str,
        spec: AssetSpec,
        width: int,
        height: int,
    ) -> AdCopy:
        instructions = prompt_service.build_copy_instructions(spec, width, height)
        encoded = base64.b64encode(image_png).decode("ascii")
        brief = f"BRIEF (your only source of facts):\n{source_text.strip()}"
        problems: list[str] = []

        for attempt in range(1, MAX_ATTEMPTS + 1):
            content: list[dict] = [
                {"type": "input_text", "text": brief},
                # Low detail is enough to judge mood and find clear space, and
                # costs a fraction of a full-resolution read.
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{encoded}",
                    "detail": "low",
                },
            ]
            if problems:
                content.append({
                    "type": "input_text",
                    "text": (
                        "Your previous attempt was rejected:\n- "
                        + "\n- ".join(problems)
                        + "\nRewrite it so none of these apply."
                    ),
                })

            copy = await self._call(instructions, content)
            problems = check_copy(copy, source_text, spec)
            if not problems:
                logger.info(
                    "Copy accepted on attempt %d: %r", attempt, copy.headline
                )
                return copy
            logger.warning("Copy rejected on attempt %d: %s", attempt, problems)

        raise CopyGenerationError(
            "The generated copy did not meet the accuracy and length rules.",
            details={"violations": problems},
        )

    async def _call(self, instructions: str, content: list[dict]) -> AdCopy:
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                text_format=AdCopy,
            )
        except OpenAIError as exc:
            logger.exception("Copy request failed")
            raise CopyGenerationError(f"The copy model request failed: {exc}") from exc

        if response.output_parsed is None:
            raise CopyGenerationError("The copy model returned no usable copy.")
        return response.output_parsed
