"""Prompt construction and source-text policy.

Two prompts, for two different jobs:

* :func:`build_copy_instructions` briefs a **text model** that reads the
  photograph, the source text and the placement, and decides what the headline
  should say. This is where copywriting judgement lives.
* :func:`build_render_prompt` briefs the **image model**, and contains only the
  final approved words. It never sees the source text -- that is what stops the
  render from transcribing the input instead of distilling it.

Keeping them apart matters: an image model handed a block of source text will
set that text on the image, because a literal string near the end of an image
prompt reads as "render this".
"""

from __future__ import annotations

import re

from app.core.errors import InsufficientSourceTextError
from app.domain.platforms import AssetSpec, AssetType

# Below this, there is nothing honest to build a headline from.
MIN_SOURCE_WORDS = 5
# Below this we still render, but the caller should know the copy will be thin.
THIN_SOURCE_WORDS = 12

MAX_ALT_TEXT_CHARS = 125

# Headline / supporting-line word budgets. Small canvases get fewer words
# because anything longer stops being legible at render size.
_WORD_BUDGETS: dict[AssetType, tuple[int, int]] = {
    AssetType.SIDEBAR_CARD: (5, 0),
    AssetType.LANDSCAPE: (7, 10),
    AssetType.FACEBOOK_LANDSCAPE: (7, 10),
    AssetType.STORY_REEL: (7, 10),
    AssetType.HERO: (9, 14),
}
_DEFAULT_WORD_BUDGET = (8, 12)


def assess_source_text(source_text: str) -> list[str]:
    """Enforce that there is enough substance to build honest copy from.

    Raises when the text cannot support a headline; returns warnings when it
    only just can.
    """
    cleaned = source_text.strip()
    if not cleaned:
        raise InsufficientSourceTextError(
            "source_text is empty. The source text is the only permitted basis "
            "for the ad copy, so nothing can be generated."
        )

    word_count = len(cleaned.split())
    if word_count < MIN_SOURCE_WORDS:
        raise InsufficientSourceTextError(
            f"source_text contains only {word_count} words, which is not enough "
            "to write a headline without inventing information.",
            details={"word_count": word_count, "minimum": MIN_SOURCE_WORDS},
        )

    if word_count < THIN_SOURCE_WORDS:
        return [
            f"source_text is short ({word_count} words); the generated copy will "
            "be minimal by design rather than padded with unsupported claims."
        ]
    return []


def derive_alt_text(source_text: str, spec: AssetSpec) -> str | None:
    """Alt text taken verbatim from the source text, never invented.

    Only produced for assets whose platform requires it.
    """
    if not spec.requires_alt_text:
        return None
    first_sentence = re.split(r"(?<=[.!?])\s+", source_text.strip())[0].strip()
    if not first_sentence:
        return None
    if len(first_sentence) <= MAX_ALT_TEXT_CHARS:
        return first_sentence
    return first_sentence[: MAX_ALT_TEXT_CHARS - 1].rstrip() + "…"


def word_budget(spec: AssetSpec) -> tuple[int, int]:
    return _WORD_BUDGETS.get(spec.asset_type, _DEFAULT_WORD_BUDGET)


# --------------------------------------------------------------------------
# Stage 1 -- the copywriter (text model, sees the photograph)


def build_copy_instructions(
    spec: AssetSpec, width: int, height: int
) -> str:
    """Brief the model that decides what the headline should say."""
    headline_words, support_words = word_budget(spec)

    support_rule = (
        f"A supporting line of at most {support_words} words is optional. Use "
        "it only when it adds something the headline cannot carry. When in "
        "doubt, return null -- one strong line beats two weak ones."
        if support_words
        else "Return null for the supporting line. This placement is too small "
        "for a second line of text."
    )

    layout = f" {spec.layout_guidance}" if spec.layout_guidance else ""

    return f"""\
You are a senior advertising copywriter. You are shown a photograph and given a
brief. Decide the words that will be set over that photograph.

DISTIL — DO NOT TRANSCRIBE
The brief is raw input, not copy. It is usually a paragraph; your headline is a
few words. Never return the brief, a sentence from it, or a lightly reworded
version of it. Find the one idea worth saying and say it in as few words as
possible.
- Headline: at most {headline_words} words. Fewer is better.
- {support_rule}
- No trailing full stop on the headline. No quotation marks around it.
- No call-to-action ("Book Now", "Call Today", "Get a Quote"). Something else
  handles that. Write the message, not the button.

ACCURACY — NON-NEGOTIABLE
The brief is your only source of facts. You may rewrite its language however you
like; you may not add information it does not contain. Never state or imply:
prices, discounts or amounts; offers, deadlines, urgency or scarcity;
statistics, ratings, review counts or awards; guarantees, warranties or
certifications; superlatives ("best", "#1", "leading", "fastest"); services,
products or qualities not mentioned; or any brand name, phone number, address
or URL not present in the brief.
Before returning a line, check: can a reader point to the part of the brief that
makes this true? If not, rewrite it. Paraphrase freely; invent nothing.

USE THE PHOTOGRAPH
Look at what is actually in frame. The headline should feel like it belongs to
this image, not to any stock photo. Also decide where the text should sit: pick
the region with calm, uncluttered space, away from faces and the main subject.

WRITE FOR THE PLACEMENT
This runs as {spec.label} on {spec.platform.value}, at {width}x{height}px.{layout}
It has about one second to land. Plain, confident, human language. Cut corporate
filler ("solutions", "we strive to deliver", "your one-stop shop"). No clickbait
and no manufactured urgency.

Return the headline, the optional supporting line, the placement region, and the
exact fragment of the brief that supports your headline.\
"""


# --------------------------------------------------------------------------
# Stage 2 -- the renderer (image model, never sees the source text)

_PLACEMENT_PHRASING = {
    "top_left": "in the upper-left area",
    "top_center": "across the upper area",
    "top_right": "in the upper-right area",
    "center_left": "on the left side, vertically centred",
    "center": "in the centre of the frame",
    "center_right": "on the right side, vertically centred",
    "bottom_left": "in the lower-left area",
    "bottom_center": "across the lower area",
    "bottom_right": "in the lower-right area",
}


def build_render_prompt(
    *,
    headline: str,
    subheadline: str | None,
    placement: str,
    spec: AssetSpec,
    width: int,
    height: int,
) -> str:
    """Brief the image model with the final words only.

    Deliberately short. Image models follow concise prompts far better than
    long rule lists, and there is no source text here to copy.
    """
    where = _PLACEMENT_PHRASING.get(placement, "over a calm area of the image")

    second_line = (
        f'\nSupporting line, set smaller beneath it: "{subheadline}"'
        if subheadline
        else ""
    )

    layout = f"\n{spec.layout_guidance}" if spec.layout_guidance else ""

    return f"""\
Set this text over the supplied photograph, {where}.

Headline: "{headline}"{second_line}

Set exactly those words. Do not add, remove, reword or repeat any of them, and
do not add any other text.

Leave the photograph itself untouched: same colours, lighting, contrast, crop,
framing, people, faces and objects. Only the text is new.

Make the typography exceptional — work a brand-campaign art director would sign
off, not default type dropped on a photo:
- Choose a typeface with real character that suits the mood of this photograph.
- Make the headline dominant, with decisive size and weight contrast against any
  supporting line.
- Take the colour from the photograph: clean white or near-black, or an accent
  hue already present in the image.
- Consider lifting one key word with weight, size or colour so the line has a
  focal point.
- Align to a clear axis, break lines at natural phrase boundaries, keep line
  lengths even, and give it generous space.

Required: instantly legible, correct spelling, no garbled or doubled letters,
type upright and horizontal, at least 5% clear of every edge, never across a
face or the main subject. Where the background is busy, a soft, edgeless
darkening behind the text is allowed for contrast — never a visible panel, bar
or box.

Add nothing but the letters: no call-to-action, button, logo, badge, icon,
emoji, QR code, shape, frame, border, divider, sticker or decorative graphic.
Do not stretch, skew, arch or 3D-extrude the type. No drop shadows, bevels,
glows, outlines or metallic gradients.

Placement: {spec.label} on {spec.platform.value}, {width}x{height}px.{layout}\
"""
